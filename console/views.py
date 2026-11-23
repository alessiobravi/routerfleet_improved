"""The console form and the terminal page. The WebSocket is console/consumers.py."""
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csp import csp_override

from router_manager.models import Router, RouterStatus
from user_manager.models import UserAcl

from .models import ConsoleSession

USERNAME = re.compile(r'^[A-Za-z0-9._@+\-\\]{1,100}$')


# xterm.js draws the terminal with <style> elements of its own (theme, cell
# size) and offers no way to give them a nonce. On the terminal page only,
# style elements may be inline; scripts stay 'self' and style attributes stay
# blocked, so nothing injected can run.
TERMINAL_CSP = {**settings.SECURE_CSP, 'style-src-elem': ["'self'", "'unsafe-inline'"]}


@csp_override(TERMINAL_CSP)
def render_terminal(request, context):
    return render(request, 'console/terminal.html', context)


def allowed(user):
    return UserAcl.objects.filter(user=user, user_level__gte=settings.CONSOLE_LEVEL).exists()


@never_cache
@login_required
def view_console(request):
    if not allowed(request.user):
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    router = get_object_or_404(Router, uuid=request.GET.get('uuid') or request.POST.get('router'))
    back = f'/router/details/?uuid={router.uuid}'
    if not router.is_managed:
        messages.warning(request, 'No console|A monitoring-only device has no credentials to connect with.')
        return redirect(back)
    status = RouterStatus.objects.filter(router=router).first()
    busy = [what for what, lock in (('a backup', getattr(status, 'backup_lock', None)),
                                    ('a command', getattr(status, 'command_lock', None))) if lock]
    has_backup_account = bool(router.username and (router.password or router.ssh_key_id))
    context = {'page_title': f'Console: {router.name}', 'router': router, 'busy': busy,
               'has_backup_account': has_backup_account, 'back': back, 'errors': [], 'posted': request.POST}

    if request.method == 'POST' and request.POST.get('action') == 'open':
        transport = request.POST.get('transport', 'ssh')
        account = request.POST.get('account', 'backup')
        username = request.POST.get('username', '').strip()
        errors = context['errors']
        if transport not in ('ssh', 'telnet'):
            errors.append('Choose SSH or Telnet.')
        elif transport == 'telnet':
            if not router.console_telnet:
                errors.append('Telnet is not allowed for this device: switch it on in the device settings first.')
            if request.POST.get('telnet_understood') != 'on':
                errors.append('Confirm that you understand Telnet is unencrypted.')
            # Over Telnet no stored credential is sent: the device prompt asks
            account, username = 'other', ''
        elif account == 'backup':
            if not has_backup_account:
                errors.append('This device has no backup account stored; use another account.')
            username = router.username
        elif account == 'other':
            if not USERNAME.match(username):
                errors.append('Enter the username to sign in with (letters, digits and . _ @ + - \\).')
        else:
            errors.append('Choose an account.')
        if not errors:
            session, ticket = ConsoleSession.open(request.user, router, transport, account, username)
            return render_terminal(request, {
                'page_title': f'Console: {router.name}', 'router': router, 'session': session, 'ticket': ticket,
                'ask_password': transport == 'ssh' and account == 'other', 'back': back,
                'idle_minutes': settings.CONSOLE_IDLE_SECONDS // 60})
    return render(request, 'console/console_form.html', context)
