# ADR 0009 — Auth implementation: PBKDF2 password hashing, pinned-algorithm JWT

Status: accepted (amended 2026-08-21: sliding session - 120-minute token, renewed
within 30 minutes of expiry via the X-Refreshed-Token response header; amended
2026-08-25: a fourth demo user, `admin`, whose token carries an all-tenant scope
claim - an owner decision, asked for twice)

## Context

The assignment wants hardcoded tenant users. Even demo auth should follow
published standards — this is a security case study, and the auth layer is
RLS layer 1's foundation. Constraint: prefer the stdlib over new dependencies.

## Decision

- **Password storage**: PBKDF2-HMAC-SHA256 with **600,000 iterations** — the
  exact configuration sanctioned by the OWASP Password Storage Cheat Sheet —
  via stdlib `hashlib.pbkdf2_hmac`, per-user 16-byte `os.urandom` salt, stored
  as `algorithm$iterations$salt$hash`, compared with `hmac.compare_digest`.
  OWASP's first choice is Argon2id (m=19456, t=2, p=1; also what FastAPI's own
  tutorial now demonstrates via pwdlib) — PBKDF2 is chosen here for stdlib-only
  purity, and Argon2id is the documented one-dependency upgrade path.
- **Users**: four demo identities, hashes hardcoded in `auth.py`; plaintext demo
  passwords documented only in the README, clearly labeled demo-only. Demo
  identities are not secrets; signing keys are. Three are one per tenant. The
  fourth, `admin`, is the **all-tenant** identity of the amendment below.
- **Scope is a claim, not a second code path** (amended 2026-08-25). An identity
  reads either one tenant or every tenant, and which one travels in the signed
  token as a `scope` claim - `tenant` or `all` - beside the `tenant_id` claim it
  has always carried. `Identity.all_tenants` is that claim in typed form, and it
  is the only source of all-tenant scope in the repo: `app.py` reads it off the
  verified token and hands it to `build_agent`, which binds the tools to the
  scoped or the unscoped data path once, before the model is called (ADR 0002 as
  amended). No tool argument, request field or model output can name a scope, so
  the property layer 1 has always had - "the tenant is not an input anywhere the
  LLM or the client can reach" - now covers the scope too.
  - The admin's `tenant_id` claim is the distinguished value `all-tenants`, which
    no tenant in the dataset uses. It is what the audit trail records the reader
    as, exactly as a tenant claim is for a tenant user, so every admin read is
    attributable in the same log.
  - Verification is unchanged in kind: HS256 with the algorithm list pinned. A
    tenant token hand-edited to `scope: "all"` fails the signature like any other
    tampering, which is asserted as a test.
  - A token carrying **no** `scope` claim reads as one-tenant scope: the least
    privilege, and the shape every token had before the claim existed. A `scope`
    this server never mints is refused outright rather than quietly downgraded -
    an unrecognized value is a token we did not issue, not a narrower one.
- **JWT**: HS256 via PyJWT — explicitly legitimate when the issuer is the sole
  audience (OWASP JWT Cheat Sheet). Verification pins the algorithm list
  (`jwt.decode(token, key, algorithms=["HS256"])`) per RFC 8725 section 3.1,
  rejecting `alg=none` and confusion attacks. Claims: `sub`, `tenant_id`,
  `scope`, `exp`, `iat`.
- **Session lifetime: a sliding session** (amended per issue #71).
  `auth.token_ttl_minutes` is **120**, and `auth.refresh_within_minutes` is
  **30**: an authenticated request whose token expires within that window is
  answered with a freshly signed one carrying the same `sub` and `tenant_id`.
  The original 30-minute expiry was justified only by matching the FastAPI
  tutorial — a code sample, not a threat model. For a single-issuer demo app
  there is no attack a short hard expiry meaningfully narrows: the token is not
  revocable either way, so a stolen one is usable until it lapses whatever the
  number, and no second audience exists to limit exposure to. A forced sign-out
  mid-demo, by contrast, is a real failure with a real cost. The sliding session
  takes both halves: tokens stay short-lived in absolute terms (a leaked one
  dies within two hours of the session's last use, and an abandoned session
  still lapses on its own), while an active user is never interrupted. This is
  the standard shape of a stateless JWT session — short access token, renewed
  while in use — and the honest alternative, one long flat TTL, buys the same
  continuity only by making every issued token long-lived.
- **Refresh transport**: the new token comes back on the `X-Refreshed-Token`
  response header of the request that earned it, so every authenticated endpoint
  refreshes transparently and there is no `/refresh` route and no client timer.
  CORS exposes the header to the SPA, and `lib/api.ts` adopts it into the
  session brick in the one place that already attaches the bearer token; the
  boot-time `exp` check stays as the client's own staleness guard. Refreshing
  runs the same pinned-algorithm decode as verification, so an expired or forged
  token refreshes nothing and is still a 401. A token is verified once per
  request, at its start: the `/chat` SSE generator never re-checks, so a turn
  already streaming completes even if the clock passes `exp` mid-stream.
- **Signing secret**: at least 256 bits (RFC 7518 section 3.2 requires a key
  no smaller than the hash output for HS256; generate with
  `openssl rand -hex 32`), loaded from the environment. **The app fails fast
  at startup if unset — there is no committed default**: a default signing
  secret in git is a hard-coded credential (OWASP Secrets Management, CWE-798,
  12-factor), demo or not. `.env.example` carries a placeholder and the
  generation command.

## Consequences

- Zero new dependencies for auth (PyJWT is required for JWT regardless).
- The startup fail-fast means `cp .env.example .env` alone is not enough to
  run — the setup docs include the one-line secret generation.
- Layer 1's JWT tampering tests (wrong signature, alg=none, expired, missing)
  map one-to-one to the RFC 8725 requirements, and the scope claim is tested the
  same way: a tenant token re-encoded with `scope: "all"` does not verify.
- The all-tenant identity is a **wider grant, not a weaker one**: it reads
  through the same validator, authorizer, read-only connection, caps, deadline
  and audit row as every other read (ADR 0002 as amended). What it demonstrates
  in the demo is precisely that - the layers enforce whatever scope the verified
  token grants, and the model still cannot influence which one that is.
- The sliding session is an **idle** timeout in OWASP's terms: a continuously
  used session renews indefinitely, because a stateless token cannot be capped
  without a server-side record of when the session actually began. That is the
  accepted gap of a demo with no revocation story; the stateless way to close it
  is a first-issued-at claim the refresh copies forward and refuses to extend
  past, which is noted here rather than built.
- Because the refresh rides on an ordinary response header, the SPA needs no
  timer and no refresh endpoint, but every client of this API must read
  `X-Refreshed-Token` to benefit — a client that ignores it degrades to a hard
  two-hour expiry rather than breaking.

## Alternatives

- **Argon2id via pwdlib/argon2-cffi** — OWASP's first choice; rejected only to
  stay stdlib-pure, explicitly noted as the production upgrade.
- **Plaintext demo passwords in code** — simpler, but off-theme and contradicts
  the sourced practice this repo commits to.
- **Server-side sessions** — no visible tenant claim crossing the trust
  boundary; JWT makes layer 1 demonstrable.
- **One long flat TTL** (a day, say) — the same "no forced sign-out" outcome
  with less machinery, but every token issued is long-lived, including the ones
  handed to a session used once and abandoned. Rejected: the sliding window
  costs one config value and one response header.
- **A dedicated `/refresh` endpoint plus a client-side timer** — the classic
  shape, and necessary when refresh and access tokens differ. Here it would add
  a route, a timer and a second token type to renew a single-audience session
  that every request already proves is alive.

## References

- OWASP Password Storage Cheat Sheet (Argon2id first; PBKDF2-HMAC-SHA256 at
  600,000 iterations) —
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP JSON Web Token Cheat Sheet (HMAC when issuer is sole audience; pin
  algorithms; secret entropy) —
  https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html
- RFC 8725, JWT Best Current Practices — https://www.rfc-editor.org/rfc/rfc8725.html
- RFC 7518, JSON Web Algorithms (HS256 key size) —
  https://www.rfc-editor.org/rfc/rfc7518.html
- OWASP Session Management Cheat Sheet (idle vs absolute timeout; a session
  expiring on inactivity rather than on a fixed clock) —
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- FastAPI security tutorial (PyJWT, HS256, openssl rand) —
  https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
- OWASP Secrets Management Cheat Sheet —
  https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- CWE-798, Use of Hard-coded Credentials — https://cwe.mitre.org/data/definitions/798.html
- 12-Factor App, Config — https://12factor.net/config
- Python hashlib docs (pbkdf2_hmac) — https://docs.python.org/3/library/hashlib.html
