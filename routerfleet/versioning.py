"""Release numbers.

The release is written as MAJOR.MINOR.PATCH ('0.8.0') in settings.py. A push
that only fixes or maintains raises PATCH; a release that brings a new feature
raises MINOR and resets PATCH.

The database stores the version as an integer (WebadminSettings), so the
release also has a number: MAJOR * 1_000_000 + MINOR * 1_000 + PATCH. 0.8.0 is
8000, above the 7516 the four-digit scheme ended at, so an installation that
upgrades never sees its version go backwards.

No Django imports: settings.py and scripts/version.py both use this.
"""
import re

RELEASE_PATTERN = re.compile(r'^(\d+)\.(\d+)\.(\d+)$')

# The oldest release that is kept. Releases before 0.10.0 were published
# before every release was scanned for known vulnerabilities, and shipped
# images with hundreds of them, so they were withdrawn: CI deletes releases,
# tags and images below this.
RELEASE_FLOOR = '0.10.0'


def parse(release):
    match = RELEASE_PATTERN.match(str(release).strip())
    if not match:
        raise ValueError(f'"{release}" is not a release number like 0.8.0')
    major, minor, patch = (int(part) for part in match.groups())
    if minor > 999 or patch > 999:
        raise ValueError(f'"{release}": MINOR and PATCH go up to 999')
    return major, minor, patch


def to_number(release):
    major, minor, patch = parse(release)
    return major * 1_000_000 + minor * 1_000 + patch


def from_number(number):
    number = int(number or 0)
    return f'{number // 1_000_000}.{number // 1_000 % 1_000}.{number % 1_000}'


def bump(release, part):
    major, minor, patch = parse(release)
    if part == 'patch':
        return f'{major}.{minor}.{patch + 1}'
    if part == 'minor':
        return f'{major}.{minor + 1}.0'
    if part == 'major':
        return f'{major + 1}.0.0'
    raise ValueError('part is patch, minor or major')


def is_retired(release):
    """True for a release number below RELEASE_FLOOR; False for anything that is
    not a release number at all, which other rules deal with."""
    try:
        return to_number(release) < to_number(RELEASE_FLOOR)
    except ValueError:
        return False
