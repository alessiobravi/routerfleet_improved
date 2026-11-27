import datetime
import json
import time

from django.contrib import messages
from django.contrib.auth.signals import user_login_failed
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from webauthn import generate_authentication_options, options_to_json, verify_authentication_response
from webauthn.helpers.structs import UserVerificationRequirement

from accounts import lockout, mfa
from user_manager.models import UserPasskey, UserTotp
from django.shortcuts import render, Http404, redirect
from django.contrib.auth.models import User
from django.contrib import auth
from .forms import CreateUserForm, LoginForm
from user_manager.models import UserAcl


def view_create_first_user(request):
    if User.objects.filter().all():
        raise Http404('Superuser already exists')
    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            # Two requests at the same moment must not both create an
            # administrator: one at a time (a locked row), checked again inside
            from django.db import IntegrityError, transaction
            from routerfleet_tools.models import WebadminSettings
            try:
                with transaction.atomic():
                    WebadminSettings.objects.get_or_create(name='webadmin_settings')
                    WebadminSettings.objects.select_for_update().get(name='webadmin_settings')
                    if User.objects.exists():
                        raise Http404('Superuser already exists')
                    new_user = User.objects.create_superuser(username=username, password=password)
                    UserAcl.objects.create(user=new_user, user_level=50)
                    if User.objects.count() != 1:
                        raise IntegrityError('another first user was created meanwhile')
            except IntegrityError:
                raise Http404('Superuser already exists') from None
            return render(request, 'accounts/superuser_created.html')
    else:
        form = CreateUserForm()
    return render(request, 'accounts/create_first_user.html', {'form': form})


BACKEND = 'django.contrib.auth.backends.ModelBackend'
PASSKEY_COOKIE = 'rf_passkey_challenge'
PASSKEY_SALT = 'routerfleet.passkey-login'


NEXT_KEY = 'rf_after_login'


def safe_next(request, url):
    """Where to go after signing in, when it is a page of this installation
    (?next=, set when an expired session was sent here) - '' otherwise: never
    another site, never //host, never the sign-in pages themselves."""
    from django.utils.http import url_has_allowed_host_and_scheme
    url = (url or '').strip()
    if (not url.startswith('/') or url.startswith(('//', '/\\', '/accounts/'))
            or not url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure())):
        return ''
    return url


def after_login(request):
    """The page to open once signed in: the one the session expired on, once."""
    return request.session.pop(NEXT_KEY, '') or '/'


def view_login(request):
    if not User.objects.filter().all():
        return redirect('/accounts/create_first_user/')
    if request.method == 'GET':
        # Kept in the session, so it survives every way of signing in
        # (password, password and code, passkey)
        wanted = safe_next(request, request.GET.get('next'))
        if wanted:
            request.session[NEXT_KEY] = wanted
        else:
            request.session.pop(NEXT_KEY, None)
    if request.method == 'POST':
        form = LoginForm(request.POST, request=request)
        if form.is_valid():
            user = form.user
            if UserTotp.objects.filter(user=user, confirmed=True).exists():
                # The password is right; the account also wants a code. Nothing
                # is signed in until the code is: the session only remembers who
                # gave the right password, and for a few minutes
                request.session['mfa_pending'] = {'user_id': user.pk, 'at': time.time()}
                return redirect('/accounts/login/verify/')
            auth.login(request, user, backend=BACKEND)
            return redirect(after_login(request))
    else:
        form = LoginForm(request=request)
    from accounts.password_reset import available
    return render(request, 'accounts/login.html', {'form': form, 'password_reset': available(),
                                                   'passkeys': passkeys_possible(request)})


def passkeys_possible(request):
    """Whether passkeys can work for this sign-in page: HTTPS, reached by a
    name (the one this browser used, not an IP address), a certificate
    browsers trust. Unknown counts as possible - behind a reverse proxy
    RouterFleet can not see the certificate; the browser decides."""
    try:
        from django.http.request import split_domain_port
        from routerfleet_tools import https
        from routerfleet_tools.models import HttpsSettings
        host = split_domain_port(request.get_host())[0]
        if not host or https._is_ip(host.strip('[]')):
            return False
        return all(value is not False for _, value in https.passkey_requirements(HttpsSettings.load(), request))
    except Exception:  # before the migrations ran, or nginx status unreadable: not offered
        return False


def _pending_user(request):
    pending = request.session.get('mfa_pending') or {}
    if time.time() - pending.get('at', 0) > mfa.PENDING_SECONDS:
        request.session.pop('mfa_pending', None)
        return None
    return User.objects.filter(pk=pending.get('user_id'), is_active=True).first()


def view_login_verify(request):
    """The second step: the code from the authenticator app."""
    user = _pending_user(request)
    if user is None:
        messages.warning(request, 'Sign in again|The sign-in was not completed in time.')
        return redirect('/accounts/login/')
    error = ''
    if request.method == 'POST':
        if lockout.is_locked_out(request, user.username):
            lockout.record_turned_away(request, user.username)
            minutes = lockout.limits()[1].seconds // 60
            error = f'Too many failed sign-in attempts. Try again in {minutes} minutes.'
        else:
            totp = UserTotp.objects.filter(user=user, confirmed=True).first()
            step = mfa.matching_step(totp.secret, request.POST.get('code'), totp.last_used_step) if totp else None
            # Claimed atomically: of two requests with the same code, one wins
            if step is not None and UserTotp.objects.filter(pk=totp.pk).filter(
                    Q(last_used_step__isnull=True) | Q(last_used_step__lt=step)).update(last_used_step=step):
                request.session.pop('mfa_pending', None)
                auth.login(request, user, backend=BACKEND)
                return redirect(after_login(request))
            # A wrong code is a refused sign-in: recorded, and counted by the lockout
            user_login_failed.send(sender=__name__, credentials={'username': user.username}, request=request)
            error = 'The code is not correct, or has already been used.'
    return render(request, 'accounts/login_verify.html', {'error': error})


@require_POST
def view_passkey_login_options(request):
    """The challenge for signing in with a passkey."""
    try:
        rp_id, _ = mfa.relying_party(request)
    except mfa.PasskeyUnavailable:
        return JsonResponse({'error': mfa.PASSKEY_UNAVAILABLE}, status=400)
    if lockout.address_locked_out(request):
        return JsonResponse({'error': 'Too many failed sign-in attempts. Try again later.'}, status=429)
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    # The challenge travels in a signed cookie that expires with it - not in a
    # session: every call here is anonymous, and a session row per call let a
    # flood of them grow the session table
    response = HttpResponse(options_to_json(options), content_type='application/json')
    response.set_signed_cookie(PASSKEY_COOKIE, f'{mfa.b64url(options.challenge)}:{time.time():.6f}', salt=PASSKEY_SALT,
                               max_age=mfa.PENDING_SECONDS, path='/accounts/passkey/', httponly=True,
                               samesite='Strict', secure=request.is_secure())
    return response


@require_POST
def view_passkey_login(request):
    """Checks the passkey's answer to the challenge and signs its owner in."""
    challenge = request.get_signed_cookie(PASSKEY_COOKIE, default=None, salt=PASSKEY_SALT,
                                          max_age=mfa.PENDING_SECONDS)
    refused = JsonResponse({'error': 'The passkey was not accepted.'}, status=400)
    refused.delete_cookie(PASSKEY_COOKIE, path='/accounts/passkey/', samesite='Strict')
    challenge, _, issued = (challenge or '').partition(':')
    try:
        issued_at = datetime.datetime.fromtimestamp(float(issued), tz=datetime.UTC)
    except ValueError:
        return refused
    if not challenge or lockout.address_locked_out(request):
        return refused
    try:
        rp_id, origin = mfa.relying_party(request)
        credential = json.loads(request.body)
        passkey = UserPasskey.objects.select_related('user').get(credential_id=credential.get('id', ''), rp_id=rp_id)
        if not passkey.user.is_active:
            raise ValueError('inactive account')
        handle = (credential.get('response') or {}).get('userHandle')
        if handle and mfa.from_b64url(handle) != str(passkey.user.pk).encode():
            raise ValueError('the passkey belongs to another account')
        verified = verify_authentication_response(
            credential=credential, expected_challenge=mfa.from_b64url(challenge),
            expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=mfa.from_b64url(passkey.public_key),
            credential_current_sign_count=passkey.sign_count, require_user_verification=True)
    except Exception:
        user_login_failed.send(sender=__name__, credentials={'username': '(passkey)'}, request=request)
        return refused
    # Single use: a passkey takes only a challenge issued after its last use -
    # an answer captured with its cookie can not be played again (many
    # passkeys do not advance their counter). Claimed atomically.
    if not UserPasskey.objects.filter(pk=passkey.pk).filter(
            Q(last_used__isnull=True) | Q(last_used__lt=issued_at)).update(
            sign_count=verified.new_sign_count, last_used=timezone.now()):
        user_login_failed.send(sender=__name__, credentials={'username': '(passkey)'}, request=request)
        return refused
    auth.login(request, passkey.user, backend=BACKEND)
    response = JsonResponse({'redirect': after_login(request)})
    response.delete_cookie(PASSKEY_COOKIE, path='/accounts/passkey/', samesite='Strict')
    return response


def view_logout(request):
    # Signing out is a POST like every other action, so a foreign page cannot
    # sign anybody out by loading this address
    if request.method != 'POST':
        return redirect('/')
    auth.logout(request)
    return render(request, 'accounts/logout.html')
