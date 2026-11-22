"""The backup tools: an audit and clean-up of stored backups, and an export of
the latest configuration of every device.

Clean-up turns stored duplicates into the same "unchanged" records that keeping
only changes creates for new backups: the run stays in the history, its content
points to the stored backup with the same configuration, and the copy is freed.
It also reconciles the files on disk with the database.
"""
import csv
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from audit_log.bulk import collect
from backup.models import BackupProfile
from backup_data.models import RouterBackup
from router_manager.models import Router

BACKUP_DIRECTORY = 'backups'


def _size_of_file(field_file):
    try:
        return field_file.size if field_file else 0
    except (OSError, ValueError):
        return 0


def _profile_of(router, default):
    return router.backup_profile or default


@dataclass
class RouterReport:
    router: Router
    stored: int = 0
    unchanged: int = 0
    duplicates: list = field(default_factory=list)   # [(backup, stored backup it repeats)]
    duplicate_bytes: int = 0
    keeps_every_backup: bool = False


@dataclass
class Report:
    routers: list = field(default_factory=list)
    orphan_files: list = field(default_factory=list)     # [(relative name, size)]
    missing_files: list = field(default_factory=list)    # [backup]
    broken_links: list = field(default_factory=list)     # [(backup, stored backup it can point to or None)]
    include_every_profile: bool = False

    @property
    def duplicates(self):
        return sum(len(r.duplicates) for r in self.routers if self.applies_to(r))

    @property
    def duplicate_bytes(self):
        return sum(r.duplicate_bytes for r in self.routers if self.applies_to(r))

    @property
    def orphan_bytes(self):
        return sum(size for _, size in self.orphan_files)

    @property
    def reclaimable_bytes(self):
        return self.duplicate_bytes + self.orphan_bytes

    @property
    def relinkable(self):
        return sum(1 for _, target in self.broken_links if target)

    @property
    def nothing_to_do(self):
        return not (self.duplicates or self.orphan_files or self.missing_files or self.relinkable)

    def applies_to(self, router_report):
        return self.include_every_profile or not router_report.keeps_every_backup


def _stored_with(backup):
    """A stored backup of the same device with the same configuration."""
    return RouterBackup.objects.filter(router_id=backup.router_id, same_as__isnull=True,
                                       backup_text_hash=backup.backup_text_hash) \
        .exclude(backup_text='').exclude(pk=backup.pk).order_by('-created').first()


def audit(include_every_profile=False):
    """What the stored backups hold, and what a clean-up would free. Reads only."""
    default = BackupProfile.objects.filter(name='default').first()
    report = Report(include_every_profile=include_every_profile)

    for router in Router.objects.select_related('backup_profile').order_by('name'):
        profile = _profile_of(router, default)
        entry = RouterReport(router=router, keeps_every_backup=bool(profile and profile.keep_unchanged))
        last_stored = None
        backups = RouterBackup.objects.filter(router=router, success=True).exclude(backup_text_hash='') \
            .order_by('created', 'id')
        for backup in backups:
            if backup.same_as_id:
                entry.unchanged += 1
                continue
            if not backup.backup_text:
                # No content of its own and no link: a lost link, repaired below
                continue
            if last_stored is not None and backup.schedule_type != 'instant' \
                    and backup.backup_text_hash == last_stored.backup_text_hash:
                # The same configuration as the stored backup before it: a copy
                entry.duplicates.append((backup, last_stored))
                entry.duplicate_bytes += len((backup.backup_text or '').encode('utf-8')) + \
                    _size_of_file(backup.backup_binary)
            else:
                entry.stored += 1
                last_stored = backup
        if entry.stored or entry.unchanged or entry.duplicates:
            report.routers.append(entry)

    # Unchanged records whose stored backup is gone (a link set to nothing):
    # point them at a stored backup of the same device with the same configuration
    for backup in RouterBackup.objects.filter(success=True, same_as__isnull=True, backup_text='') \
            .exclude(backup_text_hash='').filter(Q(backup_binary='') | Q(backup_binary__isnull=True)):
        report.broken_links.append((backup, _stored_with(backup)))

    # Files on disk and the database, reconciled both ways
    referenced = set(RouterBackup.objects.exclude(backup_binary='').exclude(backup_binary__isnull=True)
                     .values_list('backup_binary', flat=True))
    directory = os.path.join(settings.MEDIA_ROOT, BACKUP_DIRECTORY)
    if os.path.isdir(directory):
        for entry_name in sorted(os.listdir(directory)):
            path = os.path.join(directory, entry_name)
            relative = f'{BACKUP_DIRECTORY}/{entry_name}'
            if os.path.isfile(path) and not os.path.islink(path) and relative not in referenced:
                report.orphan_files.append((relative, os.path.getsize(path)))
    for backup in RouterBackup.objects.exclude(backup_binary='').exclude(backup_binary__isnull=True):
        if not backup.backup_binary.storage.exists(backup.backup_binary.name):
            report.missing_files.append(backup)
    return report


def clean(include_every_profile=False):
    """Frees what the audit found, and returns the report it acted on with what
    was freed. Recorded as one entry in the change log."""
    report = audit(include_every_profile)
    freed = {'duplicates': 0, 'bytes': 0, 'orphans': 0, 'missing': 0, 'relinked': 0}
    with collect('Backup clean-up') as batch:
        for entry in report.routers:
            if not report.applies_to(entry):
                continue
            for backup, stored in entry.duplicates:
                freed['bytes'] += len((backup.backup_text or '').encode('utf-8')) + _size_of_file(backup.backup_binary)
                if backup.backup_binary:
                    backup.backup_binary.delete(save=False)
                backup.backup_text = ''
                backup.same_as = stored
                backup.save()
                freed['duplicates'] += 1
        for relative, size in report.orphan_files:
            path = os.path.join(settings.MEDIA_ROOT, relative)
            # Only plain files directly in the backup directory, as the audit listed them
            if os.path.dirname(os.path.realpath(path)) == os.path.realpath(
                    os.path.join(settings.MEDIA_ROOT, BACKUP_DIRECTORY)) and os.path.isfile(path):
                os.remove(path)
                freed['orphans'] += 1
                freed['bytes'] += size
        for backup in report.missing_files:
            backup.backup_binary = None
            backup.save(update_fields=['backup_binary'])
            freed['missing'] += 1
        for backup, _ in report.broken_links:
            # Chosen now, after the duplicates were freed: a backup that held the
            # configuration during the audit may just have become unchanged itself
            target = _stored_with(backup)
            if target:
                backup.same_as = target
                backup.save(update_fields=['same_as'])
                freed['relinked'] += 1
        for name, count in (('Duplicate backups freed', freed['duplicates']), ('Orphan files removed', freed['orphans']),
                            ('Missing files cleared', freed['missing']), ('Links repaired', freed['relinked'])):
            if count:
                batch.counted(name, count)
    return report, freed


def _safe_name(text, fallback):
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', str(text)).strip('._')
    return name[:80] or fallback


def export_latest(include_binary=True, group=None):
    """A zip with the latest configuration of every device - a folder per device,
    one file per backup - and a manifest. Returns (open temporary file, count).
    The file deletes itself when closed."""
    routers = Router.objects.order_by('name')
    if group is not None:
        routers = routers.filter(routergroup=group)
    archive_file = tempfile.TemporaryFile()
    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(['router', 'address', 'type', 'backup_time', 'unchanged_since', 'sha256', 'files'])
    count = 0
    with zipfile.ZipFile(archive_file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for router in routers:
            latest = RouterBackup.objects.filter(router=router, success=True).exclude(backup_text_hash='') \
                .order_by('-created', '-id').first()
            if latest is None:
                continue
            content = latest.content
            folder = _safe_name(router.name, f'router-{router.id}')
            files = []
            if content.backup_text:
                name = _safe_name(content.backup_text_filename or f'{folder}.txt', f'{folder}.txt')
                archive.writestr(f'{folder}/{name}', content.backup_text)
                files.append(name)
            if include_binary and content.backup_binary and content.backup_binary.storage.exists(content.backup_binary.name):
                name = _safe_name(os.path.basename(content.backup_binary.name), f'{folder}.bin')
                with content.backup_binary.open('rb') as source, archive.open(f'{folder}/{name}', 'w') as target:
                    for chunk in iter(lambda: source.read(1 << 20), b''):
                        target.write(chunk)
                files.append(name)
            if not files:
                continue
            count += 1
            writer.writerow([router.name, router.address, router.router_type,
                             timezone.localtime(latest.created).strftime('%Y-%m-%d %H:%M'),
                             timezone.localtime(content.created).strftime('%Y-%m-%d %H:%M') if latest.same_as_id else '',
                             latest.backup_text_hash, ' '.join(files)])
        archive.writestr('manifest.csv', manifest.getvalue())
    archive_file.seek(0)
    if count:
        with collect('Backup export') as batch:
            batch.counted('Device configurations exported', count)
    return archive_file, count
