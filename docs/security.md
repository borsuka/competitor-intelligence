# Security

What is defended, how, and — equally important — what is not.

---

## Threat model

Four attackers shape the design:

1. **A customer probing another customer's data.** They hold a valid session and can send
   any request. This is the highest-likelihood attack against a B2B SaaS, and the whole
   tenancy model exists for it.
2. **Anyone with an account, using the crawler as a weapon.** The product takes a URL and
   fetches it server-side. Untreated, that is an SSRF primitive pointed at our own
   network and our cloud metadata endpoint.
3. **A competitor's website.** Sentinel reads attacker-controlled HTML and feeds it to a
   language model. The page can try to talk to the model, or to the user through it.
4. **Someone who has stolen a token.** A leaked session cookie, an exfiltrated refresh
   token, a database dump.

---

## Tenancy

Every organization-scoped route puts the organization id in the path, and the dependency
chain that produces a handler's arguments is the authorization check:

```python
current_user → require_member(org_id) → TenantScope → service(scope, ...)
```

* A handler cannot run without a `TenantScope`, because it is a required argument.
* A service cannot query without one, because every query filters `organization_id`.
* Every organization-owned table carries `organization_id` directly, even where it could
  be reached through a join, so a filter never depends on a join being written.

**A non-member gets 404, not 403.** A 403 confirms the organization exists, which turns
the endpoint into an id oracle. `tests/integration/test_api_flow.py::TestTenantIsolation`
asserts both directions: a valid competitor id under the wrong org, and a valid org id
the caller does not belong to.

Roles: `owner` (billing, delete org) → `admin` (members, competitors, alerts) →
`member` (create, analyse) → `viewer` (read). Checked with `scope.require(Role.X)` in the
service, not in the router, so a second caller of the same service cannot skip it.

---

## Authentication

| | |
|---|---|
| Hashing | Argon2id, 64 MiB memory cost, plus a deployment pepper held outside the database |
| Access token | JWT, 15 minutes, `HttpOnly; Secure; SameSite=Lax` |
| Refresh token | JWT, 30 days, rotated on every use, stored as SHA-256 |
| Reuse detection | Presenting an already-used refresh token revokes the entire family |
| Password reset | Single-use hashed token, revokes all sessions on success |

The frontend never sees a token. An XSS bug in the web app cannot read the session,
because the cookie is `HttpOnly` — which is why no endpoint returns a token in a body,
and an integration test asserts that.

**User enumeration** is closed on the paths that would otherwise leak: login returns one
message for "no such user" and "wrong password", and hashes a dummy password so the two
take the same time; password reset returns the same response whether or not the address
exists.

---

## CSRF

Sessions are cookies, so the browser attaches them to cross-site requests. Defence is a
double-submit token: a readable `sentinel_csrf` cookie that must be echoed in
`X-CSRF-Token` on every unsafe method. A cross-origin page can cause the cookie to be
sent but cannot read it to set the header.

Session-establishing endpoints (login, register, refresh, password reset) are exempt,
because no CSRF cookie exists yet and they require credentials an attacker's form does
not have.

---

## SSRF

The single most dangerous surface in this product, because the URL is user input and the
fetch is server-side.

```
url → scheme, port, credential and hostname checks
    → resolve DNS ourselves, inspect every A/AAAA record
    → reject private, loopback, link-local, CGNAT, reserved, ULA
    → fetch with redirects DISABLED
    → re-validate every redirect hop through the same guard
    → content-type allow-list, 2 MB cap, 15 s timeout
```

Blocked: non-`http(s)` schemes, credentials in URL, non-standard ports, `localhost`,
RFC1918, `100.64/10`, `169.254/16` (which covers `169.254.169.254`), IPv6 loopback and
ULA, IPv4-mapped IPv6, single-label hostnames, and `.local` / `.internal` / `.corp`
suffixes.

Two details that are easy to get wrong and are handled here:

* **Redirects are followed manually.** Letting the HTTP client follow them would open a
  socket to a destination nothing validated. "Public URL 302s to the metadata endpoint" is
  the classic bypass.
* **Errors do not say what was found.** A message like "192.168.1.50 is private" turns the
  crawler into an internal network scanner with a helpful response channel. Every rejection
  returns the same opaque message.

The webhook alert channel runs through the same guard, because it is the same attack with
a different entry point.

`tests/unit/test_urls.py` covers every vector above.

**Known limitation.** There is a TOCTOU window between DNS validation and connection: a
hostname could resolve to a public address for the guard and a private one for the socket
(DNS rebinding). Closing it fully requires pinning the connection to the validated IP with
a custom transport. It is not implemented. The practical impact is limited — the response
is not echoed back to the attacker, and the crawler only follows `text/html` — but it is a
real gap, stated rather than hidden.

---

## Prompt injection

Scraped text is attacker-controlled and is fed to a language model. Four independent
layers, because none is sufficient alone:

1. **Fencing.** Content is wrapped in `<untrusted_content id="{random nonce}">`. The nonce
   is fresh per request, so content cannot close the fence.
2. **Neutralisation.** Zero-width and bidi characters stripped, control characters removed,
   fence lookalikes and role markers (`System:`, `Assistant:`) defanged, pathological
   repetition collapsed.
3. **Schema validation.** Output is parsed into a Pydantic model. A successful injection
   still cannot produce a field the application will act on.
4. **Detection and disclosure.** Injection-like text is flagged on the analysis and shown
   in the UI — the user is told their competitor's site is trying to talk to AI systems.

Detection is deliberately non-blocking. Rejecting a page on a pattern match would silently
drop real data whenever marketing copy happened to trip a regex.

---

## Never fabricating data

A competitor intelligence tool that invents facts is worse than no tool, because the user
acts on it. Two mechanisms:

* **Prompt-level.** Every prompt carries the same house rules: use only supplied content,
  never invent a number, leave a field empty rather than guessing, distinguish what a site
  claims from what is true.
* **Code-level.** `enforce_observed_prices` compares every extracted amount against the
  prices the crawler actually parsed. An amount that was never seen is discarded and the
  plan becomes "custom pricing", with a note explaining the correction. This is enforced
  after the model returns, so it holds regardless of what the model does.

Scores follow the same principle: a dimension with no data is `null` and is excluded from
the mean, never counted as zero.

---

## Abuse and cost

| Control | Where |
|---|---|
| Rate limits per route class (auth, write, analysis, read) | Redis fixed window, keyed by session, falling back to IP |
| Per-organization quotas (competitors, analyses, pages, tokens) | PostgreSQL counters, checked **before** a job is enqueued |
| One running analysis per competitor | Enforced at enqueue |
| Unchanged content is not re-analysed | Content fingerprint on the analysis row |
| Page budget, size cap, timeout, per-domain delay | Crawler configuration |

The rate limiter **fails open** if Redis is unavailable: a limiter outage should not take
the product down. That is why quotas — enforced transactionally in PostgreSQL — are the
real spend control, and the limiter is a safety net.

---

## Other controls

* **SQL injection** — SQLAlchemy bound parameters throughout; no string-built SQL anywhere,
  including the vector search, where the embedding is a bound parameter.
* **XSS** — React escapes by default; `dangerouslySetInnerHTML` appears nowhere; scraped
  text is only ever rendered as text. The API sends a restrictive CSP, `nosniff`,
  `frame-ancestors 'none'` and a strict `Referrer-Policy`.
* **Secrets** — environment only. `.env` is git-ignored. `Settings.validate_production`
  refuses to boot production with a default `SECRET_KEY`, without `COOKIE_SECURE`, with
  `DEBUG`, or with the SSRF escape hatch enabled.
* **Logging** — structured JSON with recursive redaction of keys containing `password`,
  `token`, `secret`, `key`, `cookie` and similar. Token *counts* are explicitly allowed
  through, because cost visibility is the reason they are logged.
* **Audit log** — append-only, filtered before write, never updated or deleted in code.
* **Containers** — non-root user, no compilers in the runtime image, multi-stage build.

---

## Reporting

There is no security contact configured for this repository yet. Add one — a
`SECURITY.md` with an address and a disclosure window — before this is exposed to real
users.
