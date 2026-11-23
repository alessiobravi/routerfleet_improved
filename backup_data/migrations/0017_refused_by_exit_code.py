"""Successful backups whose console records that the device refused them.

0016 recognised a stored refusal by its text - RouterOS's one-line error. Field
reports show a refused backup that still counted as successful: the console
records the backup command's non-zero exit code, but what was stored was not
that line. The exit code is the device's own verdict, so it decides here: a
"successful" backup whose console shows a non-zero exit code for a command of
its first phase (taking the text, making the files - before "Retrieving backup
files") becomes a failed one, with the reason, and so do unchanged backups that
repeated it. The clean-up of the second phase, which may fail without harm, is
not looked at. Backups from versions whose console recorded no exit codes are
left alone."""
import re

from django.db import migrations

STAMP = re.compile(r'^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] ')
EXIT = re.compile(r'^\[exit_code=(-?\d+)\]')
REASON = 'The device refused the backup command (exit code {code}: "{command}"), recorded as a success before 0.25.2'


def first_refusal(console):
    """(exit code, command) of the first refused command of the first phase."""
    command = ''
    for raw in (console or '').splitlines():
        line = STAMP.sub('', raw).strip()
        if line.startswith('=== Retrieving backup files'):
            return None
        if line.startswith('$ '):
            command = line[2:]
            continue
        match = EXIT.match(line)
        if match and int(match.group(1)) != 0:
            return int(match.group(1)), command
    return None


def refused_by_exit_code(apps, schema_editor):
    RouterBackup = apps.get_model('backup_data', 'RouterBackup')
    candidates = (RouterBackup.objects.filter(success=True, task_console_output__contains='[exit_code=')
                  .exclude(task_console_output__isnull=True).only('id', 'task_console_output'))
    refused = {}
    for backup in candidates.iterator():
        found = first_refusal(backup.task_console_output)
        if found:
            refused[backup.id] = REASON.format(code=found[0], command=found[1][:80])
    if not refused:
        return
    for backup_id in RouterBackup.objects.filter(same_as_id__in=list(refused)).values_list('id', flat=True):
        refused.setdefault(backup_id, 'An unchanged copy of a backup the device refused, recorded as a success before 0.25.2')
    for backup_id, reason in refused.items():
        RouterBackup.objects.filter(id=backup_id).update(
            success=False, error=True, backup_text='', backup_text_hash='', config_change_detected=False,
            same_as=None, error_message=reason)


class Migration(migrations.Migration):
    dependencies = [('backup_data', '0016_refused_exports_are_failures')]

    operations = [migrations.RunPython(refused_by_exit_code, migrations.RunPython.noop)]
