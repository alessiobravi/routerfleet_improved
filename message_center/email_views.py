"""Message Center: the SMTP server email notifications are sent through."""
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.utils import timezone

from user_manager.models import UserAcl

from . import email
from .models import SmtpSettings

ADMINISTRATOR = 50
HOST = re.compile(r'^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:.]+\]?$')


@login_required
def view_email_settings(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=ADMINISTRATOR).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    smtp = SmtpSettings.load()
    errors = []
    if request.method == 'POST' and request.POST.get('action') == 'test':
        if not request.user.email:
            messages.warning(request, 'No test sent|Add your email address in My Settings first.')
        else:
            try:
                email.send(request.user.email, 'RouterFleet Improved: test email',
                           'This is a test from RouterFleet Improved. Email notifications work.', smtp)
                messages.success(request, f'Test email sent|To {request.user.email}.')
            except Exception as error:   # noqa: BLE001 - the reason is shown, the password scrubbed
                messages.error(request, 'Test email failed|' + email.scrub(error, smtp).replace('|', '/')[:300])
        return redirect('/system/notifications/email/')
    if request.method == 'POST' and request.POST.get('action') == 'save':
        data = request.POST
        host = data.get('host', '').strip()
        sender = data.get('sender_address', '').strip()
        enabled = data.get('enabled') == 'on'
        try:
            port = int(data.get('port') or 0)
        except ValueError:
            port = 0
        if host and not HOST.match(host):
            errors.append('The server must be a host name or an IP address.')
        if not 1 <= port <= 65535:
            errors.append('The port must be between 1 and 65535.')
        if data.get('security') not in dict(SmtpSettings.SECURITY):
            errors.append('Choose how the connection is secured.')
        if sender:
            try:
                validate_email(sender)
            except ValidationError:
                errors.append('The sender address is not a valid email address.')
        if enabled and not (host and sender):
            errors.append('To switch email on, set at least the server and the sender address.')
        if not errors:
            candidate = SmtpSettings(pk=smtp.pk, name=smtp.name, enabled=enabled, host=host, port=port,
                                     security=data.get('security'), username=data.get('username', '').strip()[:254],
                                     sender_address=sender, password=smtp.password,
                                     sender_name=email.one_line(data.get('sender_name') or 'RouterFleet Improved', 100))
            if data.get('remove_password') == 'on':
                candidate.password = ''
            elif data.get('password'):
                candidate.password = data['password']
            elif smtp.password and (candidate.host, candidate.port, candidate.security, candidate.username) != \
                    (smtp.host, smtp.port, smtp.security, smtp.username):
                # The stored password goes only to the server it was typed for: a
                # new server, port, security or login needs it typed again
                errors.append('Not saved - the server or the login changed: type the password again '
                              '(the stored one is only sent to the server it was given for).')
            if enabled and not errors:
                # Checked before anything is saved: the connection and the login
                try:
                    email.check(candidate)
                except Exception as error:   # noqa: BLE001 - the server's answer is shown, the password scrubbed
                    errors.append('Not saved - the server did not accept these settings: '
                                  + email.scrub(error, candidate)[:300])
            if not errors:
                candidate.verified_at = timezone.now() if enabled else None
                candidate.created = smtp.created
                candidate.uuid = smtp.uuid
                candidate.save()
                messages.success(request, 'Email settings saved|' + (
                    'The server accepted the connection and the login: email notifications and password reset are on.'
                    if enabled else 'Email is off: no email notifications, no password reset.'))
                return redirect('/system/notifications/email/')
    return render(request, 'message_center/email_settings.html', {
        'page_title': 'Email notifications', 'smtp': smtp, 'errors': errors, 'posted': request.POST or None,
        'securities': SmtpSettings.SECURITY, 'has_password': bool(smtp.password)})
