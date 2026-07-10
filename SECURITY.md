# Security Policy

## Supported Versions

Django Observe Kit is pre-1.0. Security fixes are applied to the latest
released minor version. We recommend always running the most recent release.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them privately through one of the following channels:

1. **GitHub Security Advisories** (preferred): open a report via the
   ["Report a vulnerability"](https://github.com/wiphoo/Django-Observe_Kit/security/advisories/new)
   button on the repository's Security tab.
2. **Email**: <wiphoo.m@toffoli.co.th>

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce, or a proof-of-concept.
- Affected version(s) and configuration (relevant `OBSERVE_KIT` settings).

## Response Process

- We aim to acknowledge your report within **3 business days**.
- We will provide an assessment and, where applicable, a remediation timeline
  within **10 business days**.
- We follow a coordinated disclosure process: we will work with you on a fix
  and public advisory, and we will credit reporters who wish to be named.

## Scope

Because Django Observe Kit handles request context, tracing, logging, and PII
sanitization, reports involving the following are especially relevant:

- PII leaking through logs, traces, Sentry, or audit sinks.
- Trace-context injection / trace-id poisoning from untrusted edges.
- Metrics label-cardinality amplification (denial of service).
- Unauthenticated exposure of the `/metrics` or health endpoints.

See `CHANGELOG.md` for the security hardening already shipped in this area.
