from django.shortcuts import render, Http404
from .models import WebadminSettings
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
import requests

from routerlib.decorators import cron_endpoint


@cron_endpoint
def view_cron_check_updates(request):
    webadmin_settings, webadmin_settings_created = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    webadmin_settings.cron_last_run = timezone.now()
    webadmin_settings.save()

    update_check_url = getattr(settings, 'UPDATE_CHECK_URL', '')
    if not update_check_url:
        # No endpoint configured: nothing to ask, and nothing is reported. The
        # cron run itself is still recorded above, which is what the dashboard
        # reads to tell whether the scheduler is alive.
        return JsonResponse({'update_available': False})

    if webadmin_settings.last_checked is None or timezone.now() - webadmin_settings.last_checked > timezone.timedelta(
            hours=1):
        try:
            version = settings.ROUTERFLEET_VERSION / 10000
            separator = '&' if '?' in update_check_url else '?'
            url = f'{update_check_url}{separator}app=routerfleet&version={version}'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if 'update_available' in data:
                webadmin_settings.update_available = data['update_available']

                if data['update_available']:
                    webadmin_settings.latest_version = float(data['current_version']) * 10000

                webadmin_settings.last_checked = timezone.now()
                webadmin_settings.save()

                response_data = {
                    'update_available': webadmin_settings.update_available,
                    'latest_version': webadmin_settings.latest_version,
                    'current_version': settings.ROUTERFLEET_VERSION,
                }
                return JsonResponse(response_data)

        except Exception as e:
            webadmin_settings.update_available = False
            webadmin_settings.save()
            return JsonResponse({'update_available': False})

    return JsonResponse({'update_available': webadmin_settings.update_available})