from django import template
from django.conf import settings
from routerfleet_tools.models import WebadminSettings

register = template.Library()


def version_string(number):
    """7515 -> '0.7515': the same spelling scripts/version.py tags releases with,
    so the sidebar and the release page always agree."""
    number = int(number or 0)
    return f'{number // 10000}.{number % 10000:04d}'


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
        'current_version': version_string(settings.ROUTERFLEET_VERSION),
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
