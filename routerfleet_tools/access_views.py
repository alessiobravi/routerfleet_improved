"""System settings: HTTP access - who may reach the installation over HTTP and
HTTPS, by address. Ordered allow and deny entries (IPv4 and IPv6), the first
that matches decides, then the final policy (Allow unless changed). Enforced
by nginx; a change that would shut out the administrator making it is
refused. `python manage.py http_access --reset` opens access again."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from audit_log import context

from . import https
from .https_views import _denied
from .models import AccessRule, HttpsSettings

URL = '/system/https/access/'


def _rules(exclude=None, extra=None, order=None):
    rules = [(r.action, r.network) for r in (order or AccessRule.objects.all()) if r.pk != exclude]
    return rules + ([extra] if extra else [])


def _apply(request, settings_row):
    """Sends the list to nginx; says so, or why not."""
    try:
        https.write_state(settings_row)
    except OSError:
        messages.warning(request, 'Saved, not applied|The certificate volume is not shared with the application: '
                                  'take the current compose file (see HTTPS).')
        return
    messages.success(request, 'Access list sent to nginx|Applied within a few seconds.')


def _locks_out(request, rules, default):
    address, _ = context.client_address(request)
    return address and not https.access_allows(address, rules, default), address


@login_required
def view_access(request):
    denied = _denied(request)
    if denied:
        return denied
    settings_row = HttpsSettings.load()
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        rules = list(AccessRule.objects.all())
        if action == 'add':
            try:
                network = https.clean_network(request.POST.get('network'))
            except ValueError as problem:
                error = f'Not added: {problem}.'
            else:
                verdict = request.POST.get('verdict') if request.POST.get('verdict') in ('allow', 'deny') else 'deny'
                place = request.POST.get('place', 'end')
                new = (verdict, network)
                proposed = ([new] + _rules()) if place == 'start' else _rules(extra=new)
                locked, address = _locks_out(request, proposed, settings_row.access_default)
                if locked:
                    error = f'Not added: it would shut out your own address ({address}). Add an entry that allows it first.'
                else:
                    with transaction.atomic():
                        if place == 'start':
                            for index, rule in enumerate(rules, start=1):
                                AccessRule.objects.filter(pk=rule.pk).update(position=index)
                            position = 0
                        else:
                            position = (rules[-1].position + 1) if rules else 0
                        AccessRule.objects.create(action=verdict, network=network, position=position,
                                                  note=(request.POST.get('note') or '').strip()[:100])
                    _apply(request, settings_row)
                    return redirect(URL)
        elif action in ('up', 'down', 'delete'):
            rule = get_object_or_404(AccessRule, uuid=request.POST.get('uuid'))
            order = list(rules)
            index = order.index(rule)
            if action == 'delete':
                order.pop(index)
            elif action == 'up' and index > 0:
                order[index - 1], order[index] = order[index], order[index - 1]
            elif action == 'down' and index < len(order) - 1:
                order[index + 1], order[index] = order[index], order[index + 1]
            locked, address = _locks_out(request, [(r.action, r.network) for r in order], settings_row.access_default)
            if locked:
                error = f'Not changed: it would shut out your own address ({address}).'
            else:
                with transaction.atomic():
                    if action == 'delete':
                        rule.delete()
                    for position, item in enumerate(order):
                        AccessRule.objects.filter(pk=item.pk).update(position=position)
                _apply(request, settings_row)
                return redirect(URL)
        elif action == 'default':
            default = request.POST.get('default')
            if default in dict(HttpsSettings.ACCESS_POLICIES):
                locked, address = _locks_out(request, _rules(), default)
                if locked:
                    error = (f'Not changed: with Deny as the final policy your own address ({address}) would be shut '
                             'out. Add an entry that allows it first.')
                else:
                    settings_row.access_default = default
                    settings_row.save(update_fields=['access_default', 'updated'])
                    _apply(request, settings_row)
                    return redirect(URL)
    address, _ = context.client_address(request)
    return render(request, 'routerfleet_tools/access.html', {
        'page_title': 'HTTP access', 'rules': AccessRule.objects.all(), 'default': settings_row.access_default,
        'error': error, 'your_address': address, 'shared': https.volume_shared(),
        'you_allowed': https.access_allows(address, _rules(), settings_row.access_default) if address else True})
