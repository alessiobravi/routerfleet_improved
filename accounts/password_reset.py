"""Password reset by email - offered only while the mail server works.

Asking for a link answers the same whether an account matches or not, so the
form tells nobody which names or addresses exist. The link is valid for an
hour (PASSWORD_RESET_TIMEOUT) and once: it is bound to the current password,
so it stops working when the password changes. Using it does not sign anybody
in - a second factor still applies at the next sign-in. An address asking too
often is turned away for a while.
"""
import logging

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)
REQUESTS_PER_ADDRESS = 5
WINDOW_SECONDS = 900


def available():
    from message_center.models import SmtpSettings
    try:
        return SmtpSettings.load().ready
    except Exception:  # before the migrations ran
        return False


def _too_often(request):
    from audit_log import context
    address, _ = context.client_address(request)
    key = f'rf-password-reset:{address or "unknown"}'
    count = cache.get(key, 0) + 1
    cache.set(key, count, WINDOW_SECONDS)
    return count > REQUESTS_PER_ADDRESS


def view_password_reset(request):
    if not available():
        raise Http404('Password reset is not available')
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()[:254]
        if name and not _too_often(request):
            from message_center import email
            users = User.objects.filter(is_active=True).exclude(email='').filter(
                Q(username__iexact=name) | Q(email__iexact=name))[:5]
            for user in users:
                link = request.build_absolute_uri(
                    f'/accounts/reset/{urlsafe_base64_encode(force_bytes(user.pk))}/{default_token_generator.make_token(user)}/')
                try:
                    email.send(user.email, 'RouterFleet Improved: reset your password',
                               f'Somebody - hopefully you - asked to reset the password of the account "{user.username}".\n\n'
                               f'To choose a new password, open this link within an hour:\n{link}\n\n'
                               'It works once. If you did not ask for it, ignore this message: your password stays as it is.')
                except Exception:   # noqa: BLE001 - the answer is the same either way
                    logger.warning('A password reset email could not be sent.', exc_info=True)
        return redirect('/accounts/password_reset/sent/')
    return render(request, 'accounts/password_reset.html', {})


def view_password_reset_sent(request):
    return render(request, 'accounts/password_reset_sent.html', {})


class ResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = '/accounts/reset/done/'
    post_reset_login = False


def view_password_reset_done(request):
    return render(request, 'accounts/password_reset_done.html', {})
