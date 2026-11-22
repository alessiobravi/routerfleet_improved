"""Message Center: the SMTP server email notifications are sent through."""
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render

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
        return redirect('/message_center/email/')
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
            smtp.enabled, smtp.host, smtp.port = enabled, host, port
            smtp.security = data.get('security')
            smtp.username = data.get('username', '').strip()[:254]
            smtp.sender_address = sender
            smtp.sender_name = email.one_line(data.get('sender_name') or 'RouterFleet Improved', 100)
            if data.get('remove_password') == 'on':
                smtp.password = ''
            elif data.get('password'):
                smtp.password = data['password']
            smtp.save()
            messages.success(request, 'Email settings saved|' + ('Notifications are sent by email.' if smtp.ready
                                                                 else 'Email notifications are off.'))
            return redirect('/message_center/email/')
    return render(request, 'message_center/email_settings.html', {
        'page_title': 'Email notifications', 'smtp': smtp, 'errors': errors, 'posted': request.POST or None,
        'securities': SmtpSettings.SECURITY, 'has_password': bool(smtp.password)})
