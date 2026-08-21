# ADR 0009 — Auth implementation: PBKDF2 password hashing, pinned-algorithm JWT

Status: accepted

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
- **Users**: three demo identities (one per tenant), hashes hardcoded in
  `auth.py`; plaintext demo passwords documented only in the README, clearly
  labeled demo-only. Demo identities are not secrets; signing keys are.
- **JWT**: HS256 via PyJWT — explicitly legitimate when the issuer is the sole
  audience (OWASP JWT Cheat Sheet). Verification pins the algorithm list
  (`jwt.decode(token, key, algorithms=["HS256"])`) per RFC 8725 section 3.1,
  rejecting `alg=none` and confusion attacks. Claims: `sub`, `tenant_id`,
  `exp` (30 minutes, matching the FastAPI tutorial), `iat`.
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
  map one-to-one to the RFC 8725 requirements.

## Alternatives

- **Argon2id via pwdlib/argon2-cffi** — OWASP's first choice; rejected only to
  stay stdlib-pure, explicitly noted as the production upgrade.
- **Plaintext demo passwords in code** — simpler, but off-theme and contradicts
  the sourced practice this repo commits to.
- **Server-side sessions** — no visible tenant claim crossing the trust
  boundary; JWT makes layer 1 demonstrable.

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
- FastAPI security tutorial (PyJWT, HS256, 30-minute expiry, openssl rand) —
  https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
- OWASP Secrets Management Cheat Sheet —
  https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- CWE-798, Use of Hard-coded Credentials — https://cwe.mitre.org/data/definitions/798.html
- 12-Factor App, Config — https://12factor.net/config
- Python hashlib docs (pbkdf2_hmac) — https://docs.python.org/3/library/hashlib.html
