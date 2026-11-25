#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def run_as_application_user():
    """Started as root in the container ("docker compose exec routerfleet python
    manage.py ..."): the command runs as the application user, as the
    application does - so nothing it writes (a database journal, a state file)
    ends up owned by root where the application can not write it.
    APP_USER=root keeps root, as the application itself then runs."""
    if not hasattr(os, 'geteuid') or os.geteuid() != 0 or os.environ.get('APP_USER', 'routerfleet') == 'root':
        return
    try:
        import pwd
        user = pwd.getpwnam('routerfleet')
    except (ImportError, KeyError):
        return   # not the container image (development)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.environ['HOME'] = user.pw_dir


def main():
    """Run administrative tasks."""
    run_as_application_user()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'routerfleet.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
