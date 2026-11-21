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
        for vuln in sorted(vulns, key=lambda v: (ORDER.index(v['Severity']), v['VulnerabilityID'])):
            fixed = vuln.get('FixedVersion') or 'no fix yet'
            lines.append(f'  - {vuln["VulnerabilityID"]} {vuln["Severity"].lower()} — '
                         f'{vuln["PkgName"]} {vuln.get("InstalledVersion", "")} ({fixed})')
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
