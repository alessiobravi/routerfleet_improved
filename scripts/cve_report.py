#!/usr/bin/env python3
"""The "known vulnerabilities" section of a release's notes, from Trivy reports.

    scripts/cve_report.py TRIVY_VERSION report.json [report.json ...]

Each report is `trivy image --format json` of one published image. The release
was only built because none of them has a critical or high vulnerability (and
the database image none with a fix), so this lists what remains: medium and
low findings, and for the database image the ones its distribution has not
fixed yet. Standard library only.
"""
import datetime
import json
import sys
from collections import Counter

ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']
# Longer lists than this are folded away in the release notes
FOLD_AFTER = 10


def findings(report):
    for result in report.get('Results') or []:
        for vuln in result.get('Vulnerabilities') or []:
            yield vuln


def section(trivy_version, reports):
    today = datetime.date.today().isoformat()
    lines = ['## Known vulnerabilities at release', '',
             f'Every image of this release was scanned with Trivy {trivy_version} on {today}. '
             'None of RouterFleet\'s own images has a critical or high vulnerability, and the '
             'database image has none for which a fix is published.', '']
    for report in reports:
        name = report.get('ArtifactName', '?')
        vulns = list(findings(report))
        counts = Counter(v['Severity'] for v in vulns)
        unfixed = [v for v in vulns if not v.get('FixedVersion')]
        if not vulns:
            lines.append(f'- **{name}** — none')
            continue
        summary = ', '.join(f'{counts[s]} {s.lower()}' for s in ORDER if counts[s])
        detail = f'{len(unfixed)} without a published fix' if unfixed else 'all with a fix'
        lines.append(f'- **{name}** — {summary} ({detail})')
        # One line per vulnerability, with every package it was found in: the
        # same CVE in four libraries of one source package is one problem
        grouped = {}
        for vuln in vulns:
            key = (vuln['VulnerabilityID'], vuln['Severity'], vuln.get('FixedVersion') or '')
            grouped.setdefault(key, []).append(vuln['PkgName'])
        entries = []
        for (cve, severity, fixed), packages in sorted(
                grouped.items(), key=lambda kv: (ORDER.index(kv[0][1]), kv[0][0])):
            entries.append(f'  - {cve} {severity.lower()} — {", ".join(sorted(set(packages)))}'
                           f' ({"fixed in " + fixed if fixed else "no fix yet"})')
        if len(entries) > FOLD_AFTER:
            lines.append('')
            lines.append(f'  <details><summary>{len(entries)} vulnerabilities</summary>')
            lines.append('')
            lines.extend(entries)
            lines.append('')
            lines.append('  </details>')
            lines.append('')
        else:
            lines.extend(entries)
    return '\n'.join(lines) + '\n'


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    reports = []
    for path in argv[2:]:
        with open(path, encoding='utf-8') as handle:
            reports.append(json.load(handle))
    sys.stdout.write(section(argv[1], reports))


if __name__ == '__main__':
    main(sys.argv)
