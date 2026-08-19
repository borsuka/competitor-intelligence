# Security policy

## Reporting a vulnerability

**Do not open a public issue.** Report privately through
[GitHub's private vulnerability reporting](https://github.com/borsuka/competitor-intelligence/security/advisories/new),
which is enabled on this repository.

> **Before this is exposed to real users, add a monitored email address here.** Not
> everyone who finds a bug has a GitHub account, and a reporter who cannot find a way to
> tell you quietly may tell someone else instead.

What to expect:

| | |
|---|---|
| Acknowledgement | within 3 working days |
| Initial assessment | within 7 working days |
| Fix or mitigation plan | communicated with the assessment |

Please include what you did, what happened, and what you expected — a request and a
response are worth more than a description. If you have a proof of concept, say so; do not
attach exploit code to the first message.

## Scope

In scope: this repository — the API, the workers, the crawler, the frontend, the container
and CI configuration.

Out of scope: findings against third-party services this project can talk to (Anthropic,
Voyage, a mail provider), and denial of service through sheer volume. Report those to the
service in question.

## Please do not

* Run automated scanners against a deployment you do not own.
* Access, modify or exfiltrate data belonging to anyone but yourself.
* Use a finding to reach further into an environment than needed to demonstrate it.

Testing against your own local instance is always fine, and
[docs/development.md](docs/development.md) explains how to run the whole stack.

## Known limitations

These are documented rather than hidden, and are not news:

* **DNS rebinding in the crawler.** The SSRF guard resolves and validates every hostname
  before fetching, and re-validates each redirect hop, but a window remains between
  validation and connection. Closing it needs a transport that pins the connection to the
  validated address. See [docs/security.md](docs/security.md#ssrf).
* **The rate limiter fails open.** If Redis is unavailable, requests are allowed through.
  Per-organization quotas, enforced transactionally in PostgreSQL, are the real spend
  control; the limiter is a safety net.
* **No billing.** Plan limits are enforced; there is no payment integration, so plan
  changes are administrative.

## What is protected, and how

[docs/security.md](docs/security.md) documents the threat model and the controls in
detail: tenant isolation, session handling, CSRF, the SSRF guard, prompt injection
defence, and the mechanisms that stop the AI layer from inventing facts.
