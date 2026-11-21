#!/usr/bin/env python3
"""The release number, read from the one place it is defined.

routerfleet/settings.py holds ROUTERFLEET_RELEASE as MAJOR.MINOR.PATCH. A push
that fixes or maintains raises PATCH; a release with a new feature raises MINOR
and resets PATCH. Every push to main raises one of them.

    scripts/version.py                    print the release
    scripts/version.py check              exit 1 unless CHANGELOG.md has a section for it
    scripts/version.py notes              print the CHANGELOG.md section of it
    scripts/version.py newer-than TAG...  exit 1 unless it is above every vX.Y.Z tag given
    scripts/version.py bump patch|minor   raise it in settings.py
    scripts/version.py title              the GitHub Release title
    scripts/version.py retired TAG        exit 0 if TAG is a release older than this one

Standard library only, so CI can run it before anything is installed.
"""
import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = ROOT / 'routerfleet' / 'settings.py'
RELEASE_LINE = re.compile(r"^ROUTERFLEET_RELEASE\s*=\s*'([^']+)'\s*$", re.MULTILINE)

_spec = importlib.util.spec_from_file_location('versioning', ROOT / 'routerfleet' / 'versioning.py')
versioning = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(versioning)


def release():
    match = RELEASE_LINE.search(SETTINGS.read_text(encoding='utf-8'))
    if not match:
        sys.exit('ROUTERFLEET_RELEASE not found in routerfleet/settings.py')
    versioning.parse(match.group(1))
    return match.group(1)


def changelog_section(version):
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)', text,
                      re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def release_title(version):
    """'RouterFleet Improved X.Y.Z', followed by the label of its changelog
    heading when it has one: '## [X.Y.Z] - date - First public release'."""
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(rf'^## \[{re.escape(version)}\] - [0-9-]+(?: - (.+))?$', text, re.MULTILINE)
    label = match.group(1).strip() if match and match.group(1) else ''
    return f'RouterFleet Improved {version}' + (f' - {label}' if label else '')


def main(argv):
    command = argv[1] if len(argv) > 1 else 'print'
    current = release()
    if command == 'print':
        print(current)
    elif command == 'check':
        if not changelog_section(current):
            sys.exit(f'CHANGELOG.md has no section "## [{current}]" - add one before releasing.')
        print(f'{current}: changelog section present')
    elif command == 'notes':
        section = changelog_section(current)
        if not section:
            sys.exit(f'CHANGELOG.md has no section for {current}')
        print(section)
    elif command == 'newer-than':
        released = []
        for tag in argv[2:]:
            try:
                released.append(versioning.to_number(tag.removeprefix('refs/tags/').removeprefix('v')))
            except ValueError:
                continue  # not a release tag
        if released and versioning.to_number(current) <= max(released):
            sys.exit(f'{current} is not above the latest release {versioning.from_number(max(released))} '
                     f'- raise it with: scripts/version.py bump patch (or minor for a feature)')
        print(f'{current}: above every release')
    elif command == 'title':
        print(release_title(current))
    elif command == 'retired':
        # exit 0 when the tag given is a release older than the one in settings.py:
        # only the newest release is kept
        tag = argv[2].removeprefix('refs/tags/').removeprefix('v') if len(argv) > 2 else ''
        sys.exit(0 if versioning.is_retired(tag, release()) else 1)
    elif command == 'bump':
        part = argv[2] if len(argv) > 2 else ''
        new = versioning.bump(current, part)
        text = SETTINGS.read_text(encoding='utf-8')
        SETTINGS.write_text(RELEASE_LINE.sub(f"ROUTERFLEET_RELEASE = '{new}'", text, count=1), encoding='utf-8')
        print(f'{current} -> {new}  (now add "## [{new}] - <date>" to CHANGELOG.md)')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
