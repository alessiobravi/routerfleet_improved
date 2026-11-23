"""Backups whose text is a device's refusal, not a configuration.

Before 0.24.2 a text backup was whatever the device printed, its exit code
ignored: RouterOS 6, sent "/export show-sensitive" (a RouterOS 7 word), answered
"expected end of command (line 1 column 10)" - and that line was stored as a
successful backup. Such backups become failed ones, with the reason; the line
is no configuration, and as a device's newest "configuration" it would be the
one retention keeps. Unchanged backups that repeated one follow it. Only a text
that is a single RouterOS command error is touched."""
import re

from django.db import migrations

REFUSAL = re.compile(r'^\s*(expected end of command|bad command name \S+|syntax error|no such item'
                     r'|input does not match any value of \S+)[^\n]*\(line \d+ column \d+\)\s*$', re.IGNORECASE)
REASON = ('The device refused the backup command and its error message was stored as the configuration '
          '(before 0.24.2): {line}')


def refused_exports_are_failures(apps, schema_editor):
    RouterBackup = apps.get_model('backup_data', 'RouterBackup')
    candidates = RouterBackup.objects.filter(success=True, backup_text__contains='(line ').only('id', 'backup_text')
    refused = {}
    for backup in candidates.iterator():
        if REFUSAL.match(backup.backup_text or ''):
            refused[backup.id] = backup.backup_text.strip()
    if not refused:
        return
    repeats = RouterBackup.objects.filter(same_as_id__in=list(refused))
    for backup_id in list(repeats.values_list('id', flat=True)):
        refused.setdefault(backup_id, 'an unchanged copy of such a backup')
    for backup_id, line in refused.items():
        RouterBackup.objects.filter(id=backup_id).update(
            success=False, error=True, backup_text='', backup_text_hash='', config_change_detected=False,
            same_as=None, error_message=REASON.format(line=line[:200]))


class Migration(migrations.Migration):
    dependencies = [('backup_data', '0015_backup_list_indexes'), ('router_manager', '0032_device_type_export_words_by_version')]

    operations = [migrations.RunPython(refused_exports_are_failures, migrations.RunPython.noop)]
