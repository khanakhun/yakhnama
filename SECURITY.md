# Security Policy

Yakhnama handles reports about hazards, moderator decisions and, potentially, personal
information about reporters. We take security issues seriously and appreciate responsible
disclosure.

## Supported versions

This project is pre-release. Only the `main` branch is supported; there are no tagged
release lines to patch yet.

| Version | Supported |
|---------|-----------|
| `main`  | yes |

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Email **kashanghoriii@gmail.com** with the subject prefix `[yakhnama security]`. Please
include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce it, including any request, payload, or configuration needed.
- The affected commit or branch.
- Whether the issue involves personal data (for example reporter identity or contact
  information) or could put a reporter's safety at risk — flag this explicitly, it changes
  how we prioritise the response.
- Your preferred contact method and, if you would like credit, how to attribute you.

If you can, encrypt sensitive details or ask for a secure channel in your first message
before sending exploit details.

## Response targets

- **Acknowledgement** within 5 days of your report.
- **Triage** (confirmed, severity assessed) within 14 days.
- **Fix or a documented mitigation plan** within 90 days, sooner for anything involving
  personal data exposure or reporter safety, which are prioritised above all other work.

We will keep you updated as we work through triage and remediation.

## Scope

In scope: this repository, the Yakhnama backend — its API, authentication and
authorisation, data storage, report intake and moderation workflow, and its dependencies.

Personal-data exposure (for example casualty names appearing where they must not, or
reporter contact details leaking) and anything that could put a reporter's safety at risk
are explicitly in scope and are prioritised over other severity classes, even when their
technical severity looks moderate.

Out of scope: infrastructure not in this repository (frontend, mobile, ML, early-warning
systems — see `README.md` for what this repository is not), and social engineering against
maintainers or contributors.

## Safe harbour

We will not pursue legal action against anyone who reports a vulnerability in good faith,
in accordance with this policy: making a reasonable, good-faith effort to avoid privacy
violations, data destruction and service disruption, not exploiting the vulnerability
beyond what is needed to demonstrate it, and reporting promptly and directly to us rather
than disclosing publicly first. We will work with you to understand and resolve the issue
before any public disclosure.

## Bug bounty

There is no bug bounty program at this time.
