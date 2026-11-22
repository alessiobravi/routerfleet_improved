from .models import WebadminSettings
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
import requests

from routerfleet.versioning import to_number
from routerlib.decorators import cron_endpoint


def check_enabled():
    return bool(getattr(settings, 'UPDATE_CHECK_URL', '')) or bool(getattr(settings, 'UPDATE_CHECK', True))


def _ask_custom_endpoint(url):
    """An operator's own endpoint: answers update_available and current_version."""
    separator = '&' if '?' in url else '?'
    response = requests.get(f'{url}{separator}app=routerfleet&version={settings.ROUTERFLEET_RELEASE}', timeout=10)
    response.raise_for_status()
    data = response.json()
    if 'update_available' not in data:
        raise ValueError('no update_available in the answer')
    latest = to_number(data['current_version']) if data['update_available'] else None
    return bool(data['update_available']), latest


def _ask_github():
    """The latest release of the project on GitHub. Only the newest release is
    published there, so "latest" is exactly the version to update to."""
    response = requests.get(settings.UPDATE_RELEASES_API, timeout=10, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'RouterFleet-Improved-update-check'})
    response.raise_for_status()
    latest = to_number(str(response.json()['tag_name']).removeprefix('v'))
    return latest > settings.ROUTERFLEET_VERSION, latest


# "Check for updates" in System settings may ask again after this long: the
# button can not turn into a stream of requests to GitHub
MANUAL_CHECK_SECONDS = 30


def run_check(webadmin_settings):
    """Asks the update source now and stores the answer. Returns '' or why
    the check failed (the previous answer then stands)."""
    webadmin_settings.last_checked = timezone.now()
    custom = getattr(settings, 'UPDATE_CHECK_URL', '')
    try:
        available, latest = _ask_custom_endpoint(custom) if custom else _ask_github()
    except Exception as error:
        webadmin_settings.save(update_fields=['last_checked'])
        return str(error)[:200] or error.__class__.__name__
    webadmin_settings.update_available = available
    if latest is not None:
        webadmin_settings.latest_version = latest
    webadmin_settings.save()
    return ''


def check_now(webadmin_settings):
    """The check asked for by hand: (done, message)."""
    if not check_enabled():
        return False, 'The update check is turned off (UPDATE_CHECK in .env).'
    last = webadmin_settings.last_checked
    if last and timezone.now() - last < timezone.timedelta(seconds=MANUAL_CHECK_SECONDS):
        wait = MANUAL_CHECK_SECONDS - int((timezone.now() - last).total_seconds())
        return False, f'Checked a moment ago: try again in {max(wait, 1)} seconds.'
    error = run_check(webadmin_settings)
    if error:
        return False, f'The update source did not answer ({error}). The previous answer stands.'
    return True, ''


@cron_endpoint
def view_cron_check_updates(request):
    webadmin_settings, _ = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    webadmin_settings.cron_last_run = timezone.now()
    webadmin_settings.save()

    if not check_enabled():
        # Turned off: nothing is asked and nothing is reported. The cron run is
        # still recorded above, which is how the status page tells whether the
        # scheduler is alive. A flag left over from before is cleared, or it
        # would be shown forever.
        if webadmin_settings.update_available:
            webadmin_settings.update_available = False
            webadmin_settings.save(update_fields=['update_available'])
        return JsonResponse({'update_available': False})

    # At most once an hour - counted from the last attempt, so a source that is
    # down is not asked on every scheduler run
    if webadmin_settings.last_checked is None or \
            timezone.now() - webadmin_settings.last_checked > timezone.timedelta(hours=1):
        if run_check(webadmin_settings):
            # The previous answer stands; the next attempt comes in an hour
            return JsonResponse({'update_available': webadmin_settings.update_available})

    return JsonResponse({
        'update_available': webadmin_settings.update_available,
        'latest_version': webadmin_settings.latest_version,
        'current_version': settings.ROUTERFLEET_VERSION,
    })
