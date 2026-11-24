"""The Tools section: maintenance an administrator runs by hand."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import filesizeformat
from django.utils import timezone

from router_manager.models import RouterGroup
from routerlib.actions import posted_action, posted_confirmation
from user_manager.models import UserAcl

from . import backup_tools

ADMINISTRATOR = 50


def _denied(request):
    if not UserAcl.objects.filter(user=request.user, user_level__gte=ADMINISTRATOR).exists():
        return render(request, 'access_denied.html', {'page_title': 'Access Denied'})
    return None


@login_required
def view_tools(request):
    denied = _denied(request)
    if denied:
        return denied
    from django.db import connection
    from . import database
    if posted_action(request, 'optimize'):
        if posted_confirmation(request) != 'optimize':
            messages.warning(request, 'Not optimized|The confirmation was not given.')
        else:
            full = request.POST.get('full') == 'on' and connection.vendor == 'postgresql'
            try:
                before, after = database.optimize(full=full)
            except Exception as error:
                import logging
                logging.getLogger(__name__).warning('Database optimization failed', exc_info=True)
                messages.error(request, f'Not optimized|The database refused it ({error.__class__.__name__}); see the log.')
            else:
                messages.success(request, 'Database optimized|'
                                 + database.outcome(before, after, full, connection.vendor).replace('|', '/'))
        return redirect('/tools/')
    from backup_data.models import RouterBackup
    return render(request, 'routerfleet_tools/tools.html', {
        'page_title': 'Tools', 'database_size': database.size(), 'database_vendor': connection.vendor,
        'backup_count': RouterBackup.objects.count()})


@login_required
def view_backup_cleanup(request):
    denied = _denied(request)
    if denied:
        return denied
    include_every_profile = request.POST.get('include_every_profile') == 'on' or \
        request.GET.get('include_every_profile') == 'on'
    if posted_action(request, 'delete_binaries'):
        from router_manager.models import DeviceType
        slugs = [s for s in request.POST.getlist('types') if DeviceType.objects.filter(slug=s).exists()]
        if posted_confirmation(request) != 'delete_binaries' or not slugs:
            messages.warning(request, 'Nothing deleted|Choose the device types and confirm.')
        else:
            count, size = backup_tools.delete_binaries(slugs)
            messages.success(request, f'Binary backups deleted|{count} files, {filesizeformat(size)} freed. '
                                      'The text backups are kept.')
        return redirect('/tools/backup_cleanup/')
    if posted_action(request, 'clean'):
        if posted_confirmation(request) != 'clean':
            messages.warning(request, 'Nothing cleaned|Type clean to confirm.')
        else:
            _, freed = backup_tools.clean(include_every_profile)
            messages.success(request, (
                f"Clean-up done|{freed['duplicates']} duplicate backups freed, {freed['orphans']} orphan files removed, "
                f"{freed['missing']} missing files cleared, {freed['relinked']} links repaired - "
                f"{filesizeformat(freed['bytes'])} freed."))
        suffix = '?include_every_profile=on' if include_every_profile else ''
        return redirect('/tools/backup_cleanup/' + suffix)
    report = backup_tools.audit(include_every_profile)
    return render(request, 'routerfleet_tools/backup_cleanup.html',
                  {'page_title': 'Backup clean-up', 'report': report, 'include_every_profile': include_every_profile,
                   'binaries': backup_tools.binary_summary()})


@login_required
def view_backup_export(request):
    denied = _denied(request)
    if denied:
        return denied
    if request.method == 'POST':
        group = None
        if request.POST.get('group'):
            group = get_object_or_404(RouterGroup, uuid=request.POST.get('group'))
        archive, count = backup_tools.export_latest(include_binary=request.POST.get('include_binary') == 'on',
                                                     group=group)
        if not count:
            archive.close()
            messages.warning(request, 'Nothing exported|No device has a stored backup to export.')
            return redirect('/tools/backup_export/')
        name = f"routerfleet-latest-backups-{timezone.localtime().strftime('%Y%m%d-%H%M')}.zip"
        return FileResponse(archive, as_attachment=True, filename=name, content_type='application/zip')
    return render(request, 'routerfleet_tools/backup_export.html',
                  {'page_title': 'Export latest backups', 'groups': RouterGroup.objects.order_by('name')})
