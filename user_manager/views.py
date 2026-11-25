import json
import time

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from webauthn import generate_registration_options, options_to_json, verify_registration_response
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from accounts import mfa
from user_manager.models import UserPasskey, UserTotp
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from user_manager.models import UserAcl
from .forms import UserAclForm, UserAddressLinkForm
from django.contrib import messages
from django.contrib.sessions.models import Session
from routerlib.actions import posted_action, posted_confirmation


@login_required
def view_user_settings(request):
    """The settings a user changes for themselves, no user level required."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from message_center import email as email_notifications
    from message_center.models import SmtpSettings
    user_acl, created = UserAcl.objects.get_or_create(user=request.user)
    if request.method == 'POST' and request.POST.get('action') == 'email':
        address = request.POST.get('email', '').strip()
        if SmtpSettings.load().ready:
            events = {field for field, _, _ in email_notifications.PERSONAL_EVENTS if request.POST.get(field) == 'on'}
        else:
            # The switches are greyed out (not sent) until email works: the choices are kept
            channel = email_notifications.personal_channel(request.user)
            events = {field for field, _, _ in email_notifications.PERSONAL_EVENTS if getattr(channel, field, False)}
        try:
            if address:
                validate_email(address)
        except ValidationError:
            messages.warning(request, 'Not saved|The email address is not valid.')
            return redirect('/user/settings/')
        request.user.email = address
        request.user.save(update_fields=['email'])
        email_notifications.save_personal_channel(request.user, address, events)
        messages.success(request, 'Email notifications saved|' + (
            f'{len(events)} kind{"s" if len(events) != 1 else ""} of notification to {address}.' if address and events
            else 'You receive no email notifications.'))
        return redirect('/user/settings/')
    if request.method == 'POST' and request.POST.get('action') == 'session':
        try:
            hours = int(request.POST.get('session_hours', ''))
        except ValueError:
            hours = None
        if hours in dict(UserAcl.SESSION_HOURS):
            user_acl.session_hours = hours
            user_acl.save(update_fields=['session_hours', 'updated'])
            messages.success(request, f'Session expiration time saved|You stay signed in for {dict(UserAcl.SESSION_HOURS)[hours]} '
                                      f'after your last use.')
        return redirect('/user/settings/')
    if request.method == 'POST' and request.POST.get('action') == 'test_email':
        try:
            email_notifications.send(request.user.email, 'RouterFleet Improved: test email',
                                     'This is a test from RouterFleet Improved. Your email notifications work.')
            messages.success(request, f'Test email sent|To {request.user.email}.')
        except Exception as error:   # noqa: BLE001 - the reason is shown, the password scrubbed
            messages.error(request, 'Test email failed|' + email_notifications.scrub(error, SmtpSettings.load()).replace('|', '/')[:300])
        return redirect('/user/settings/')
    form = UserAddressLinkForm(request.POST or None, instance=user_acl)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Settings saved|Your settings have been updated.')
        return redirect('/user/settings/')
    context = {
        'page_title': 'My Settings', 'form': form,
        'totp_enabled': UserTotp.objects.filter(user=request.user, confirmed=True).exists(),
        'passkeys': UserPasskey.objects.filter(user=request.user).order_by('created'),
        'user_level_label': user_acl.get_user_level_display() if user_acl.user_level else 'No access level',
        'email_events': [(field, label, sub, bool(getattr(email_notifications.personal_channel(request.user), field, False)))
                         for field, label, sub in email_notifications.PERSONAL_EVENTS],
        'email_ready': SmtpSettings.load().ready,
        'session_choices': UserAcl.SESSION_HOURS, 'session_hours': user_acl.session_hours,
        'theme_choices': (('auto', 'Automatic', 'fa-adjust', 'Follows this device: dark when it is dark.'),
                          ('light', 'Light', 'fa-sun', 'Always light.'),
                          ('dark', 'Dark', 'fa-moon', 'Always dark - for the night.')),
    }
    return render(request, 'user_manager/user_settings.html', context)


@login_required
def password_attempt_refused(request, password):
    """The current password typed again in a signed-in session (to add a
    passkey, to change it): counted like a sign-in - wrong ones recorded in
    the login history, and refused once the sign-in lockout applies - so a
    session left open can not be used to guess it. '' when it is right."""
    from django.contrib.auth.signals import user_login_failed
    from accounts import lockout
    if lockout.is_locked_out(request, request.user.username):
        lockout.record_turned_away(request, request.user.username)
        return 'Too many wrong passwords - try again later.'
    if not password or not request.user.check_password(password):
        user_login_failed.send(sender=__name__, credentials={'username': request.user.username}, request=request)
        return 'That is not your password.' if password else 'Type your password.'
    return ''


def view_password_change(request):
    """A user changes their own password: the current one first, then the new
    one twice, checked against the password rules. The other sessions of the
    account end; this one stays signed in."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST':
        refused = password_attempt_refused(request, request.POST.get('old_password', ''))
        if refused:
            form.is_valid()
            form.add_error('old_password', refused)
            return render(request, 'user_manager/password_change.html', {'page_title': 'Change password', 'form': form})
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Password changed|Other devices signed in with this account have to sign in again.')
        return redirect('/user/settings/')
    return render(request, 'user_manager/password_change.html', {'page_title': 'Change password', 'form': form})


@login_required
def view_totp_setup(request):
    """Turning the authenticator app on (scan, then confirm with a code) or off
    (with a code, which proves the app is still at hand)."""
    totp, _ = UserTotp.objects.get_or_create(user=request.user, defaults={'secret': mfa.new_totp_secret()})
    if request.method == 'POST':
        step = mfa.matching_step(totp.secret, request.POST.get('code'), totp.last_used_step)
        if posted_action(request, 'disable') and totp.confirmed:
            if step is None:
                messages.warning(request, 'Not turned off|The code is not correct.')
                return redirect('/user/settings/totp/')
            totp.delete()
            messages.success(request, 'Authenticator app turned off|Signing in asks for your password only.')
            return redirect('/user/settings/')
        if posted_action(request, 'enable') and not totp.confirmed:
            if step is None:
                messages.warning(request, 'Not turned on|The code is not correct; check the time on your phone.')
                return redirect('/user/settings/totp/')
            totp.confirmed, totp.last_used_step, totp.confirmed_at = True, step, timezone.now()
            totp.save()
            messages.success(request, 'Authenticator app turned on|Signing in now asks for a code as well.')
            return redirect('/user/settings/')
    context = {'page_title': 'Authenticator App', 'totp': totp}
    if not totp.confirmed:
        uri = mfa.provisioning_uri(totp.secret, request.user.username)
        context.update(qr_svg=mfa.qr_svg(uri), secret=' '.join(totp.secret[i:i + 4] for i in range(0, len(totp.secret), 4)))
    return render(request, 'user_manager/totp_setup.html', context)


@login_required
@require_POST
def view_passkey_options(request):
    """The options for creating a new passkey - after the account's password:
    a passkey signs in on its own, so a session left open must not be enough to
    add one."""
    try:
        password = str(json.loads(request.body or b'{}').get('password') or '')
    except (ValueError, AttributeError):
        password = ''
    refused = password_attempt_refused(request, password)
    if refused:
        return JsonResponse({'error': refused}, status=403)
    try:
        rp_id, _ = mfa.relying_party(request)
    except mfa.PasskeyUnavailable:
        return JsonResponse({'error': mfa.PASSKEY_UNAVAILABLE}, status=400)
    existing = [PublicKeyCredentialDescriptor(id=mfa.from_b64url(passkey.credential_id))
                for passkey in UserPasskey.objects.filter(user=request.user, rp_id=rp_id)]
    options = generate_registration_options(
        rp_id=rp_id, rp_name=mfa.RP_NAME, user_id=str(request.user.pk).encode(),
        user_name=request.user.username, exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED))
    request.session['passkey_register'] = {'challenge': mfa.b64url(options.challenge), 'at': time.time()}
    return HttpResponse(options_to_json(options), content_type='application/json')


@login_required
@require_POST
def view_passkey_add(request):
    """Stores the passkey the browser created, once its answer checks out."""
    state = request.session.pop('passkey_register', None) or {}
    if time.time() - state.get('at', 0) > mfa.PENDING_SECONDS:
        return JsonResponse({'error': 'The request expired. Try again.'}, status=400)
    try:
        rp_id, origin = mfa.relying_party(request)
        payload = json.loads(request.body)
        verified = verify_registration_response(
            credential=payload['credential'], expected_challenge=mfa.from_b64url(state['challenge']),
            expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    except Exception:
        return JsonResponse({'error': 'The passkey could not be verified.'}, status=400)
    name = (str(payload.get('name') or '').strip() or 'Passkey')[:100]
    UserPasskey.objects.create(
        user=request.user, name=name, rp_id=rp_id, sign_count=verified.sign_count,
        credential_id=mfa.b64url(verified.credential_id), public_key=mfa.b64url(verified.credential_public_key))
    messages.success(request, f'Passkey added|"{name}" can now be used to sign in.')
    return JsonResponse({'redirect': '/user/settings/'})


@login_required
@require_POST
def view_passkey_delete(request):
    passkey = get_object_or_404(UserPasskey, uuid=request.POST.get('uuid'), user=request.user)
    passkey.delete()
    messages.success(request, f'Passkey removed|"{passkey.name}" can no longer be used to sign in.')
    return redirect('/user/settings/')


@login_required
def view_user_list(request):
    if not UserAcl.objects.filter(user=request.user).filter(user_level__gte=50).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    page_title = 'User Manager'
    from django.db.models import Count, Exists, OuterRef
    from .models import UserTotp
    user_acl_list = UserAcl.objects.select_related('user').annotate(
        passkeys=Count('user__passkeys', distinct=True),
        app=Exists(UserTotp.objects.filter(user=OuterRef('user'), confirmed=True))).order_by('user__username')
    context = {'page_title': page_title, 'user_acl_list': user_acl_list}
    return render(request, 'user_manager/list.html', context)


@login_required
def view_manage_user(request):
    if not UserAcl.objects.filter(user=request.user).filter(user_level__gte=50).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    user_acl = None
    user = None
    if 'uuid' in request.GET:
        user_acl = get_object_or_404(UserAcl, uuid=request.GET['uuid'])
        user = user_acl.user
        form = UserAclForm(instance=user, initial={'user_level': user_acl.user_level}, user_id=user.id)
        page_title = 'Edit User ' + user.username
        if posted_action(request, 'reset_mfa'):
            # For an account whose phone or passkeys are lost: back to password only
            if posted_confirmation(request) == user.username:
                removed = UserPasskey.objects.filter(user=user).delete()[0]
                had_totp = UserTotp.objects.filter(user=user).delete()[0]
                messages.success(request, f'Second factors reset|{user.username} signs in with a password only now '
                                          f'({"authenticator app removed, " if had_totp else ""}{removed} passkey(s) removed).')
            else:
                messages.warning(request, 'Not reset|Invalid confirmation')
            return redirect(f'/user/manage/?uuid={user_acl.uuid}')
        if posted_action(request, 'delete'):
            username = user.username
            if posted_confirmation(request) == username:
                user.delete()
                messages.success(request, 'User deleted|The user ' + username + ' has been deleted.')
                return redirect('/user/list/')

            return redirect('/user/list/')
    else:
        form = UserAclForm()
        page_title = 'Add User'

    if request.method == 'POST':
        if user_acl:
            form = UserAclForm(request.POST, instance=user, user_id=user.id)
        else:
            form = UserAclForm(request.POST)

        if form.is_valid():
            form.save()
            if form.cleaned_data.get('password1'):
                user_disconnected = False
                if user:
                    for session in Session.objects.all():
                        if str(user.id) == session.get_decoded().get('_auth_user_id'):
                            session.delete()
                            if not user_disconnected:
                                messages.warning(request,
                                                 'User Disconnected|The user ' + user.username + ' has been disconnected.')
                                user_disconnected = True
            if user_acl:
                messages.success(request,
                                 'User updated|The user ' + form.cleaned_data['username'] + ' has been updated.')
            else:
                messages.success(request, 'User added|The user ' + form.cleaned_data['username'] + ' has been added.')
            return redirect('/user/list/')

    mfa_state = None
    if user:
        mfa_state = {'totp': UserTotp.objects.filter(user=user, confirmed=True).exists(),
                     'passkeys': UserPasskey.objects.filter(user=user).count()}
    return render(request, 'user_manager/manage_user.html',
                  {'form': form, 'page_title': page_title, 'user_acl': user_acl, 'mfa_state': mfa_state})
