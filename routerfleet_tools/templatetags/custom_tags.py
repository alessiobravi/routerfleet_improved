from django import template
from django.conf import settings
from routerfleet_tools.models import WebadminSettings

register = template.Library()


def version_string(number):
    """8001 -> '0.8.1': the stored integer spelled as the release it stands for."""
    from routerfleet.versioning import from_number
    return from_number(number)


@register.simple_tag
def tag_webadmin_version():
    webadmin_settings, settings_created = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    if webadmin_settings.current_version != settings.ROUTERFLEET_VERSION:
        webadmin_settings.current_version = settings.ROUTERFLEET_VERSION
        webadmin_settings.save()
    if webadmin_settings.current_version > webadmin_settings.latest_version:
        webadmin_settings.latest_version = webadmin_settings.current_version
        webadmin_settings.save()
    if webadmin_settings.current_version == webadmin_settings.latest_version:
        webadmin_settings.update_available = False
        webadmin_settings.save()
    return {
        'current_version': settings.ROUTERFLEET_RELEASE,
        'latest_version': version_string(webadmin_settings.latest_version),
        'update_available': webadmin_settings.update_available,
        'changelog_url': getattr(settings, 'UPDATE_CHANGELOG_URL', ''),
    }


@register.simple_tag
def tag_project_repository_url():
    """Where the interface points for the project itself."""
    return getattr(settings, 'PROJECT_REPOSITORY_URL', '')


@register.simple_tag
def tag_project_support_url():
    """Where the interface points for questions and device requests."""
    return getattr(settings, 'PROJECT_SUPPORT_URL', '')


@register.simple_tag
def tag_update_state():
    """The top bar's update icon: green on the latest release, yellow when a
    newer one exists, grey when the check is off or has not run yet."""
    from dashboard.views import update_check_state
    state = update_check_state()
    release = settings.ROUTERFLEET_RELEASE
    if not state['enabled']:
        return {'color': 'text-secondary', 'title': f'RouterFleet Improved {release} - update check off', 'href': '/about/', 'external': False}
    if state['available']:
        return {'color': 'text-warning', 'title': f"Update available: RouterFleet Improved {state['latest']} (running {release})",
                'href': state['releases_url'], 'external': True}
    if state['checked']:
        return {'color': 'text-success', 'title': f'RouterFleet Improved {release} - the latest release',
                'href': state['releases_url'], 'external': True}
    return {'color': 'text-secondary', 'title': f'RouterFleet Improved {release} - updates not checked yet',
            'href': '/about/', 'external': False}
