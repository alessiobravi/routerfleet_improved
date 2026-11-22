"""System settings: HTTPS."""
import secrets
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from routerfleet.middleware import forget_https_mode
from user_manager.models import UserAcl

from . import https
from .models import HttpsSettings

ADMINISTRATOR = 50
CHALLENGE_SECONDS = 600
URL = '/system/https/'


def _denied(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=ADMINISTRATOR).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    return None


def _proposal(request, current):
    """The change asked for, validated: (proposal, errors). The private key
    stays in memory only until it is written to the volume."""
    mode = request.POST.get('mode', '')
    errors = []
    proposal = {'mode': mode, 'domain': '', 'email': '', 'trusted_proxies': '', 'certificate': None}
    if mode not in dict(HttpsSettings.MODES):
        return proposal, ['Choose a mode.']
    try:
        if mode in ('letsencrypt', 'custom') or (mode == 'selfsigned' and request.POST.get('domain', '').strip()):
            proposal['domain'] = https.clean_domain(request.POST.get('domain'))
        if mode == 'letsencrypt':
            proposal['email'] = https.clean_email(request.POST.get('email'))
            if request.POST.get('accept_terms') != 'on':
                errors.append("Accept the Let's Encrypt Subscriber Agreement.")
        if mode == 'proxy':
            proposal['trusted_proxies'] = '\n'.join(https.clean_proxies(request.POST.get('trusted_proxies')))
        if mode == 'custom':
            certificate = request.POST.get('certificate', '').strip()
            key = request.POST.get('private_key', '').strip()
            if certificate or key:
                proposal['certificate'] = https.check_certificate(certificate, key, proposal['domain'])
            elif current.mode != 'custom' or current.domain != proposal['domain']:
                errors.append('Paste the certificate (with its chain) and its private key.')
    except ValidationError as error:
        errors.extend(error.messages)
    return proposal, errors


def _consequences(request, current, proposal):
    """What changes for people, said before it is applied."""
    candidate = HttpsSettings(mode=proposal['mode'], domain=proposal['domain'],
                              custom_trusted=bool(proposal['certificate'] and proposal['certificate']['trusted'])
                              if proposal['certificate'] else current.custom_trusted)
    requirements = https.passkey_requirements(candidate, request, status={'mode': 'letsencrypt', 'certificate_issued': True}
                                              if proposal['mode'] == 'letsencrypt' else {})
    notes = []
    users = https.passkey_users()
    if not https.passkeys_work(requirements) and users:
        notes.append(f'{users} user{"s" if users != 1 else ""} sign{"" if users != 1 else "s"} in with a passkey. '
                     'Passkeys will not work in this mode: they will need their password and second factor.')
    if proposal['mode'] == 'plain':
        notes.append('Passwords, session cookies and device credentials typed into RouterFleet will cross the '
                     'network unencrypted. Anyone on the path can read them.')
    if proposal['mode'] == 'proxy':
        notes.append('RouterFleet will answer on plain HTTP. Make sure only the reverse proxy can reach it, and '
                     'that the proxy serves HTTPS to browsers.')
    if proposal['mode'] == 'selfsigned':
        notes.append('Browsers will warn that the connection is not private until the certificate is accepted on '
                     'each device.')
    if proposal['mode'] == 'letsencrypt':
        notes.append(f"Let's Encrypt must reach http://{proposal['domain']}/ on port 80 from the internet. Until the "
                     'certificate is issued, the current certificate is served.')
    if current.mode in https.TLS_MODES + ('env',) and proposal['mode'] in https.HTTPS_OFF_MODES:
        notes.append('HTTPS is being switched off.')
    return requirements, notes


@login_required
def view_https(request):
    denied = _denied(request)
    if denied:
        return denied
    current = HttpsSettings.load()
    shared = https.volume_shared()
    action = request.POST.get('action') if request.method == 'POST' else None

    if action == 'review':
        proposal, errors = _proposal(request, current)
        if errors or not shared:
            if not shared:
                errors.append('The certificate volume is not shared with the application container: see the '
                              'notice on this page.')
            return render(request, 'routerfleet_tools/https.html', _context(request, current, shared, errors=errors,
                                                                            posted=request.POST))
        requirements, notes = _consequences(request, current, proposal)
        needs_sum = proposal['mode'] in https.HTTPS_OFF_MODES
        token = secrets.token_urlsafe(16)
        first, second = 3 + secrets.randbelow(7), 11 + secrets.randbelow(29)
        request.session['https_change'] = {
            'token': token, 'expires': time.time() + CHALLENGE_SECONDS, 'answer': first + second if needs_sum else None,
            'proposal': {k: v for k, v in proposal.items() if k != 'certificate'},
            # The key waits on the certificate volume (0600), never in the session table
            'certificate': {'pending': https.store_pending_certificate(token, proposal['certificate']),
                            'subject': proposal['certificate']['subject'], 'issuer': proposal['certificate']['issuer'],
                            'not_after': proposal['certificate']['not_after'].isoformat(),
                            'names': proposal['certificate']['names'], 'trusted': proposal['certificate']['trusted']}
            if proposal['certificate'] else None,
        }
        return render(request, 'routerfleet_tools/https_review.html', {
            'page_title': 'Review the HTTPS change', 'current': current, 'proposal': proposal,
            'mode_label': dict(HttpsSettings.MODES)[proposal['mode']], 'requirements': requirements,
            'passkeys_ok': https.passkeys_work(requirements), 'notes': notes, 'token': token,
            'needs_sum': needs_sum, 'first': first, 'second': second})

    if action == 'apply':
        change = request.session.pop('https_change', None) or {}
        answer = request.POST.get('answer', '').strip()
        valid = (change.get('token') and secrets.compare_digest(change['token'], request.POST.get('token', ''))
                 and time.time() < change.get('expires', 0)
                 and (change.get('answer') is None or (answer.isdigit() and int(answer) == change['answer'])))
        if not valid:
            if change.get('token'):
                https.discard_pending_certificate(change['token'])
            messages.error(request, 'Not applied|The confirmation was wrong or expired. Nothing was changed.')
            return redirect(URL)
        proposal = change['proposal']
        if change.get('certificate'):
            certificate = change['certificate']
            if not https.activate_pending_certificate(change['token']):
                messages.error(request, 'Not applied|The reviewed certificate is no longer there. Nothing was changed.')
                return redirect(URL)
            import datetime
            current.custom_subject, current.custom_issuer = certificate['subject'], certificate['issuer']
            current.custom_not_after = datetime.datetime.fromisoformat(certificate['not_after'])
            current.custom_names, current.custom_trusted = '\n'.join(certificate['names']), certificate['trusted']
        current.mode, current.domain = proposal['mode'], proposal['domain']
        current.email, current.trusted_proxies = proposal['email'], proposal['trusted_proxies']
        current.save()
        try:
            https.write_state(current)
        except OSError as error:
            messages.error(request, f'Saved, not applied|The certificate volume could not be written: {error}')
            return redirect(URL)
        forget_https_mode()
        messages.success(request, 'HTTPS change sent to nginx|It is applied within a few seconds; this page shows '
                                  'when it is. If the browser warns or loses the connection, reload.')
        return redirect(URL)

    return render(request, 'routerfleet_tools/https.html', _context(request, current, shared))


def _context(request, current, shared, errors=None, posted=None):
    status = https.read_status()
    requirements = https.passkey_requirements(current, request, status=status or {})
    applying = bool(shared and current.mode != 'env' and (not status or status.get('generation') != current.generation))
    return {
        'page_title': 'System settings', 'current': current, 'modes': HttpsSettings.MODES, 'shared': shared,
        'status': status, 'requirements': requirements, 'passkeys_ok': https.passkeys_work(requirements),
        'passkey_users': https.passkey_users(), 'served': https.served_certificate() if current.mode == 'env' else None,
        'errors': errors or [], 'posted': posted, 'applying': applying,
    }
