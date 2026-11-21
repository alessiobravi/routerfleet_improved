#!/usr/bin/env python3
"""The release version, read from the one place it is defined.

routerfleet/settings.py holds ROUTERFLEET_VERSION as an integer (7515). The
version that is shown, tagged and released is that integer read as
<major>.<four digits>: 7515 -> 0.7515, 10002 -> 1.0002.

    scripts/version.py          print the version
    scripts/version.py check    exit 1 unless CHANGELOG.md has a section for it
    scripts/version.py notes    print the CHANGELOG.md section of that version

Standard library only, so CI can run it before anything is installed.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def version_number():
    text = (ROOT / 'routerfleet' / 'settings.py').read_text(encoding='utf-8')
    match = re.search(r'^ROUTERFLEET_VERSION\s*=\s*(\d+)\s*$', text, re.MULTILINE)
    if not match:
        sys.exit('ROUTERFLEET_VERSION not found in routerfleet/settings.py')
    return int(match.group(1))


def version_string(number):
    return f'{number // 10000}.{number % 10000:04d}'


def changelog_section(version):
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)', text,
                      re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def main(argv):
    version = version_string(version_number())
    command = argv[1] if len(argv) > 1 else 'print'
    if command == 'print':
        print(version)
    elif command == 'check':
        if not changelog_section(version):
            sys.exit(f'CHANGELOG.md has no section "## [{version}]" - add one before releasing.')
        print(f'{version}: changelog section present')
    elif command == 'notes':
        section = changelog_section(version)
        if not section:
            sys.exit(f'CHANGELOG.md has no section for {version}')
        print(section)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
