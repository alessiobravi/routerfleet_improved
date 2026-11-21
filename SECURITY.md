# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub:
**Security → Report a vulnerability** on this repository
(<https://github.com/alessiobravi/routerfleet_improved/security/advisories/new>).
Do not open a public issue for a vulnerability.

Include what you can of: the affected release, the steps to reproduce, and the
impact as you understand it. You will get an acknowledgement, and the fix and
any advisory are coordinated with you before anything is made public.

## Supported versions

Only the **latest release** receives fixes. Every release is scanned for known
vulnerabilities before it is published (see [docs/security.md](docs/security.md#known-vulnerabilities)),
and upgrading to the newest release is how an installation picks up fixes —
including an installation of an earlier RouterFleet project, whose database
upgrades in place ([docs/upgrading.md](docs/upgrading.md)).

## Scope

The application and the five container images published from this repository.
The security model — what is protected and how, and what is deliberately left
to the operator — is described in [docs/security.md](docs/security.md).

This software is provided as is, without warranty; see the
[disclaimer](README.md#disclaimer).
