"""One search for everything (the header's magnifying glass, Ctrl+K or /):
devices, groups, backup profiles, commands, SSH keys, users and the pages of
the menu, each opening where it is shown. Only what the user may open is
returned - the same levels as the pages themselves."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse

from user_manager.models import UserAcl

PER_GROUP = 6
MIN_LENGTH = 2

# label, address, lowest level that may open it, other words that find it
PAGES = (
    ('Status', '/status/', 0, 'dashboard overview home this week disk scheduler'),
    ('Devices', '/router/list/?filter_group=all', 0, 'devices manager routers hosts list'),
    ('Device groups', '/router/group_list/', 0, 'groups'),
    ('Device discovery', '/discovery/', 30, 'discover scan neighbours lldp mndp cdp'),
    ('Discovered devices', '/router/discovered/', 30, 'discovered found new'),
    ('Backups', '/backup/backup_list/', 0, 'backup history compare diff'),
    ('Backup profiles', '/backup/profile_list/', 0, 'profiles retention schedule'),
    ('Fleet Commander', '/fleet_commander/', 20, 'commands run scripts'),
    ('Jobs', '/fleet_commander/job/list/', 20, 'jobs tasks runs scheduled'),
    ('User Manager', '/user/list/', 50, 'users accounts people access levels'),
    ('SSH Keys', '/ssh_keys/', 0, 'ssh keys host keys fingerprint generate'),
    ('Message Center', '/message_center/message_history/', 0, 'messages notifications history'),
    ('Tools', '/tools/', 50, 'tools database optimize export'),
    ('System settings', '/system/', 50, 'settings system configuration'),
    ('Device types', '/system/device-types/', 50, 'device types vendors presets'),
    ('Firmware', '/system/firmware/', 40, 'firmware updates repository rollouts upgrade'),
    ('HTTPS', '/system/https/', 50, 'https certificate tls lets encrypt'),
    ('HTTP access', '/system/https/access/', 50, 'access list allow deny ban'),
    ('Notifications', '/system/notifications/', 50, 'notification channels telegram ntfy email'),
    ('Email server', '/system/notifications/email/', 50, 'smtp mail server'),
    ('Display', '/system/display/', 50, 'display rows page size'),
    ('Updates', '/system/updates/', 50, 'update routerfleet version'),
    ('Audit Log', '/audit_log/change_log/', 50, 'audit log changes who'),
    ('Sign-in history', '/audit_log/login_history/', 50, 'login sign-in history'),
    ('My Settings', '/user/settings/', 0, 'my settings password passkey two-factor session'),
    ("What's new", '/updates/', 0, 'whats new changelog releases'),
    ('About', '/about/', 0, 'about version license'),
)


def _level(user):
    return UserAcl.objects.filter(user=user).values_list('user_level', flat=True).first() or 0


def results(user, query):
    """[{'title', 'items': [{'label', 'sub', 'href', 'icon'}]}], the groups that found something."""
    from fleet_commander.models import Command
    from backup.models import BackupProfile
    from router_manager.models import Router, RouterGroup, SSHKey
    query = (query or '').strip()[:100]
    if len(query) < MIN_LENGTH:
        return []
    level = _level(user)
    words = query.lower().split()
    groups = []

    def add(title, items):
        if items:
            groups.append({'title': title, 'items': items[:PER_GROUP]})

    routers = (Router.objects.filter(Q(name__icontains=query) | Q(address__icontains=query))
               .order_by('name')[:PER_GROUP])
    add('Devices', [{'label': r.name, 'sub': f'{r.address} · {r.get_router_type_display()}',
                     'href': f'/router/details/?uuid={r.uuid}', 'icon': 'fa-server'} for r in routers])
    add('Device groups', [{'label': g.name, 'sub': 'show its devices', 'icon': 'fa-object-group',
                           'href': f'/router/list/?filter_group={g.uuid}'}
                          for g in RouterGroup.objects.filter(name__icontains=query).order_by('name')[:PER_GROUP]])
    add('Backup profiles', [{'label': p.name, 'sub': 'backup profile', 'icon': 'fa-clock-rotate-left',
                             'href': f'/backup/profile_list/?profile={p.uuid}'}
                            for p in BackupProfile.objects.filter(name__icontains=query).order_by('name')[:PER_GROUP]])
    if level >= 20:
        add('Commands', [{'label': c.name, 'sub': 'Fleet Commander', 'icon': 'fa-terminal',
                          'href': f'/fleet_commander/?command={c.uuid}'}
                         for c in Command.objects.filter(name__icontains=query).order_by('name')[:PER_GROUP]])
    add('SSH keys', [{'label': k.name, 'sub': 'SSH key', 'icon': 'fa-key', 'href': f'/ssh_keys/?key={k.uuid}'}
                     for k in SSHKey.objects.filter(name__icontains=query).order_by('name')[:PER_GROUP]])
    if level >= 50:
        acls = (UserAcl.objects.select_related('user')
                .filter(Q(user__username__icontains=query) | Q(user__first_name__icontains=query)
                        | Q(user__last_name__icontains=query) | Q(user__email__icontains=query))
                .order_by('user__username')[:PER_GROUP])
        add('Users', [{'label': a.user.username, 'sub': a.user.get_full_name() or a.get_user_level_display(),
                       'icon': 'fa-user', 'href': f'/user/list/?user={a.uuid}'} for a in acls])
    pages = [p for p in PAGES if level >= p[2] and all(w in f'{p[0]} {p[3]}'.lower() for w in words)]
    add('Pages', [{'label': label, 'sub': href.split('?')[0], 'icon': 'fa-arrow-right', 'href': href}
                  for label, href, _, _ in pages])
    return groups


@login_required
def view_search(request):
    return JsonResponse({'groups': results(request.user, request.GET.get('q', ''))})
