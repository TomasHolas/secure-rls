"""Auth brick (ADR 0009).

Demo users are stored as PBKDF2-HMAC-SHA256 digests - 600,000 iterations, per-user
16-byte salt, `algorithm$iterations$salt$hash` - and tokens are HS256 JWTs whose
algorithm list is pinned on decode.

The session slides. `create_token` mints a token that lives `auth.token_ttl_minutes`,
and `refreshed_token` re-issues one for a still-valid token that expires within
`auth.refresh_within_minutes` - so an active caller is never signed out mid-session
while tokens stay short-lived in absolute terms. Refreshing decodes with the same
pinned verification as `verify_token`: an expired or forged token refreshes nothing.

Scope (ADR 0009 as amended). An identity reads either one tenant or every tenant, and
which one it is travels in the signed token as the `scope` claim - `tenant` or `all` -
beside the `tenant_id` claim it has always carried. `Identity.all_tenants` is the typed
form of it, and it is the ONLY source of all-tenant scope in the repo: the agent binds
its tools to the scoped or the unscoped path from this flag at build time (ADR 0002 as
amended), so no tool argument, request field or model output can name or widen a scope.
The admin user's `tenant_id` claim is the distinguished value `ALL_TENANTS`, which no
tenant in the dataset uses, and it stays what the audit trail records the reader as.

Verification is unchanged in kind: the same pinned HS256 decode, so a hand-edited
`scope` fails the signature like any other tampering. A token carrying no `scope` at all
is read as one-tenant scope - the least privilege, and the shape every token had before
this claim existed - while a `scope` this module never mints is refused rather than
quietly downgraded.
"""

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from runtime import runtime

SECRET_ENV_VAR = "JWT_SECRET"
SCOPE_CLAIM = "scope"
SCOPE_TENANT = "tenant"
SCOPE_ALL = "all"
# The tenant claim of an all-scope identity: a value no tenant in the dataset carries.
ALL_TENANTS = "all-tenants"

_JWT_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["sub", "tenant_id", "exp", "iat"]
_MIN_SECRET_BYTES = 32
_SCOPES = frozenset({SCOPE_TENANT, SCOPE_ALL})

# username -> (tenant_id, scope, stored PBKDF2 hash); plaintexts live only in the README.
_DEMO_USERS: dict[str, tuple[str, str, str]] = {
    "alice@acme": (
        "acme",
        SCOPE_TENANT,
        "pbkdf2_sha256$600000$417244ce9f6e9feb891d4d97b893e1a1"
        "$171b47b7341efaa3da642d00864fe1d59dba825f85616e8dbe20332f44c7d2f0",
    ),
    "bob@beta": (
        "beta",
        SCOPE_TENANT,
        "pbkdf2_sha256$600000$70dcf225eba0585c19de6e1cf172c1ba"
        "$b6fc7d8db4bfe599d428cb768afad03b4059109bf39913d65346e12c79acf6a2",
    ),
    "carol@gamma": (
        "gamma",
        SCOPE_TENANT,
        "pbkdf2_sha256$600000$b58f0c96777c2186adf57082bd765d1b"
        "$ffbfefb8ee191009d5f933f1507b4d0a515a83f45372402e4c2886bbb827c6b8",
    ),
    "admin": (
        ALL_TENANTS,
        SCOPE_ALL,
        "pbkdf2_sha256$600000$281305efb0dd662b92186fa6224db2a9"
        "$76dd56a50ca1e1cc0ce20e731baa438cc39fb48903aa9ad4ba7e4c3bf1599789",
    ),
}


@dataclass(frozen=True)
class Identity:
    """The verified caller: subject, the tenant the RLS layers scope to, and its scope.

    `all_tenants` is that scope in typed form: false is the ordinary one-tenant identity,
    true the all-tenant one whose `tenant_id` is `ALL_TENANTS`. It defaults to the narrow
    reading, so an identity built without saying so is never an all-tenant one.
    """

    sub: str
    tenant_id: str
    all_tenants: bool = False


class AuthError(Exception):
    """Raised when a token is invalid or expired, or the signing secret is unusable."""


def jwt_secret() -> str:
    """Return the HS256 signing secret from the environment, failing fast if unset or too weak."""
    secret = os.environ.get(SECRET_ENV_VAR, "")
    if not secret:
        raise AuthError(f"{SECRET_ENV_VAR} is not set - generate one with: openssl rand -hex 32")
    if _secret_bytes(secret) < _MIN_SECRET_BYTES:
        raise AuthError(
            f"{SECRET_ENV_VAR} is too short - HS256 needs at least {_MIN_SECRET_BYTES} bytes "
            "(64 hex chars) - generate one with: openssl rand -hex 32"
        )
    return secret


def verify_password(username: str, password: str) -> Identity | None:
    """Return the identity behind valid credentials, or None for a bad password or unknown user."""
    record = _DEMO_USERS.get(username)
    if record is None:
        return None
    tenant_id, scope, stored = record
    if not _password_matches(password, stored):
        return None
    return Identity(sub=username, tenant_id=tenant_id, all_tenants=scope == SCOPE_ALL)


def create_token(identity: Identity) -> str:
    """Sign a short-lived HS256 token carrying the identity's subject, tenant and scope."""
    issued_at = datetime.now(UTC)
    claims = {
        "sub": identity.sub,
        "tenant_id": identity.tenant_id,
        SCOPE_CLAIM: SCOPE_ALL if identity.all_tenants else SCOPE_TENANT,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=runtime().auth.token_ttl_minutes),
    }
    return jwt.encode(claims, jwt_secret(), algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> Identity:
    """Decode a token with the algorithm pinned (RFC 8725), raising AuthError on any failure."""
    return _identity_of(_verified_claims(token))


def refreshed_token(token: str) -> str | None:
    """Re-issue a verified token that expires within the refresh window, else None."""
    claims = _verified_claims(token)
    remaining = datetime.fromtimestamp(claims["exp"], UTC) - datetime.now(UTC)
    if remaining > timedelta(minutes=runtime().auth.refresh_within_minutes):
        return None
    return create_token(_identity_of(claims))


def _verified_claims(token: str) -> dict:
    """The token's claims, verified with the algorithm pinned; any failure is an AuthError."""
    try:
        return jwt.decode(
            token,
            jwt_secret(),
            algorithms=[_JWT_ALGORITHM],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid or expired token: {exc}") from exc


def _identity_of(claims: dict) -> Identity:
    """The identity a verified claim set carries: the subject, its tenant and its scope."""
    return Identity(
        sub=claims["sub"], tenant_id=claims["tenant_id"], all_tenants=_all_tenants(claims)
    )


def _all_tenants(claims: dict) -> bool:
    """Whether the verified scope claim grants every tenant; anything unminted is refused."""
    scope = claims.get(SCOPE_CLAIM, SCOPE_TENANT)
    if scope not in _SCOPES:
        raise AuthError(f"the token carries an unknown scope: {scope!r}")
    return scope == SCOPE_ALL


def _password_matches(password: str, stored: str) -> bool:
    """Recompute the stored record's PBKDF2 digest and compare it in constant time."""
    algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    computed = hashlib.pbkdf2_hmac(
        algorithm.removeprefix("pbkdf2_"),
        password.encode(),
        bytes.fromhex(salt_hex),
        int(iterations),
    )
    return hmac.compare_digest(computed, bytes.fromhex(digest_hex))


def _secret_bytes(secret: str) -> int:
    """Size of the secret in bytes, hex-decoded when it is a hex string."""
    try:
        return len(bytes.fromhex(secret))
    except ValueError:
        return len(secret.encode())
