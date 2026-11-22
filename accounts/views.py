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
            new_user = User.objects.create_superuser(username=username, password=password)
            UserAcl.objects.create(user=new_user, user_level=50)
            return render(request, 'accounts/superuser_created.html')
    else:
        form = CreateUserForm()
    return render(request, 'accounts/create_first_user.html', {'form': form})


BACKEND = 'django.contrib.auth.backends.ModelBackend'


def view_login(request):
    if not User.objects.filter().all():
        return redirect('/accounts/create_first_user/')
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
            return redirect('/')
    else:
        form = LoginForm(request=request)
    return render(request, 'accounts/login.html', {'form': form})


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
                return redirect('/')
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
    request.session['passkey_login'] = {'challenge': mfa.b64url(options.challenge), 'at': time.time()}
    return HttpResponse(options_to_json(options), content_type='application/json')


@require_POST
def view_passkey_login(request):
    """Checks the passkey's answer to the challenge and signs its owner in."""
    state = request.session.pop('passkey_login', None) or {}
    refused = JsonResponse({'error': 'The passkey was not accepted.'}, status=400)
    if time.time() - state.get('at', 0) > mfa.PENDING_SECONDS or lockout.address_locked_out(request):
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
            credential=credential, expected_challenge=mfa.from_b64url(state['challenge']),
            expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=mfa.from_b64url(passkey.public_key),
            credential_current_sign_count=passkey.sign_count, require_user_verification=True)
    except Exception:
        user_login_failed.send(sender=__name__, credentials={'username': '(passkey)'}, request=request)
        return refused
    passkey.sign_count = verified.new_sign_count
    passkey.last_used = timezone.now()
    passkey.save(update_fields=['sign_count', 'last_used'])
    auth.login(request, passkey.user, backend=BACKEND)
    return JsonResponse({'redirect': '/'})


def view_logout(request):
    # Signing out is a POST like every other action, so a foreign page cannot
    # sign anybody out by loading this address
    if request.method != 'POST':
        return redirect('/')
    auth.logout(request)
    return render(request, 'accounts/logout.html')