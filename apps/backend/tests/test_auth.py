"""Auth tests: PBKDF2 credentials and pinned-algorithm JWTs (issue #21, ADR 0009)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from auth import (
    _DEMO_USERS,
    ALL_TENANTS,
    SCOPE_ALL,
    SCOPE_CLAIM,
    SCOPE_TENANT,
    SECRET_ENV_VAR,
    AuthError,
    Identity,
    create_token,
    jwt_secret,
    refreshed_token,
    verify_password,
    verify_token,
)
from runtime import runtime

TEST_SECRET = "a1" * 32
OTHER_SECRET = "b2" * 32

DEMO_CREDENTIALS = [
    ("alice@acme", "demo-acme", "acme"),
    ("bob@beta", "demo-beta", "beta"),
    ("carol@gamma", "demo-gamma", "gamma"),
]


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    """Every test signs and verifies with its own environment secret."""
    monkeypatch.setenv(SECRET_ENV_VAR, TEST_SECRET)


def _unsigned_token(claims: dict) -> str:
    """Forge an alg=none token: the RFC 8725 attack a pinned algorithm list must reject."""
    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims)}."


@pytest.mark.parametrize(("username", "password", "tenant_id"), DEMO_CREDENTIALS)
def test_correct_password_returns_identity(username, password, tenant_id):
    assert verify_password(username, password) == Identity(sub=username, tenant_id=tenant_id)


def test_the_admin_credentials_return_an_all_tenant_identity():
    """The fourth demo user is the all-scope one, and its tenant claim is the distinguished value.

    Scope is a property of the identity, not of a second user store (ADR 0009 as amended): the
    same PBKDF2 record, the same lookup, one more field on what comes back.
    """
    assert verify_password("admin", "demo-admin") == Identity(
        sub="admin", tenant_id=ALL_TENANTS, all_tenants=True
    )


def test_a_tenant_identity_is_never_all_scope_by_default():
    """The narrow reading is the default everywhere an identity is built without saying so."""
    assert Identity(sub="alice@acme", tenant_id="acme").all_tenants is False
    assert verify_password("alice@acme", "demo-acme").all_tenants is False


@pytest.mark.parametrize(
    ("identity", "scope"),
    [
        (Identity(sub="alice@acme", tenant_id="acme"), SCOPE_TENANT),
        (Identity(sub="admin", tenant_id=ALL_TENANTS, all_tenants=True), SCOPE_ALL),
    ],
)
def test_a_token_states_the_scope_it_was_signed_for(identity, scope):
    """The scope travels as a signed claim, so verification returns the identity that was minted."""
    token = create_token(identity)

    assert _claims(token)[SCOPE_CLAIM] == scope
    assert verify_token(token) == identity


def test_a_tenant_token_edited_to_claim_all_scope_fails_the_signature():
    """Layer 1's whole point: widening the scope is tampering, and tampering is refused.

    The payload is rewritten to the exact claims an admin token carries and re-attached to the
    signature the tenant token came with - the attack a client would actually try, since the SPA
    holds the token and can read it. HS256 covers the payload, so it does not verify.
    """
    header, payload, signature = create_token(
        Identity(sub="alice@acme", tenant_id="acme")
    ).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    forged = _segment({**claims, SCOPE_CLAIM: SCOPE_ALL, "tenant_id": ALL_TENANTS})

    with pytest.raises(AuthError):
        verify_token(f"{header}.{forged}.{signature}")


def test_a_token_carrying_a_scope_this_server_never_mints_is_refused():
    """An unrecognized scope is an error, never a quiet downgrade to the narrow reading."""
    issued_at = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "alice@acme",
            "tenant_id": "acme",
            SCOPE_CLAIM: "superuser",
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthError):
        verify_token(token)


def test_a_token_carrying_no_scope_at_all_reads_as_one_tenant():
    """Absence is the least privilege, which is what makes the claim safe to add to a live app."""
    assert verify_token(_token_expiring_in(60)) == Identity(sub="alice@acme", tenant_id="acme")


def _segment(payload: dict) -> str:
    """One base64url JWT segment, the way a client re-encoding a payload would produce it."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_wrong_password_returns_none():
    assert verify_password("alice@acme", "demo-beta") is None


def test_unknown_user_returns_none():
    assert verify_password("mallory@acme", "demo-acme") is None


def test_stored_hashes_match_adr_parameters():
    for tenant_id, scope, stored in _DEMO_USERS.values():
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        assert algorithm == "pbkdf2_sha256"
        assert int(iterations) == 600_000
        assert len(bytes.fromhex(salt_hex)) == 16
        assert len(bytes.fromhex(digest_hex)) == 32
        assert tenant_id in {"acme", "beta", "gamma", ALL_TENANTS}
        assert scope in {SCOPE_TENANT, SCOPE_ALL}


def test_salts_are_unique_per_user():
    salts = {stored.split("$")[2] for *_, stored in _DEMO_USERS.values()}
    assert len(salts) == len(_DEMO_USERS)


def test_token_roundtrip_preserves_subject_and_tenant():
    identity = Identity(sub="alice@acme", tenant_id="acme")
    assert verify_token(create_token(identity)) == identity


def test_token_ttl_comes_from_runtime_config():
    claims = jwt.decode(
        create_token(Identity(sub="bob@beta", tenant_id="beta")),
        TEST_SECRET,
        algorithms=["HS256"],
    )
    assert claims["exp"] - claims["iat"] == runtime().auth.token_ttl_minutes * 60


def _token_expiring_in(minutes: float, *, sub: str = "alice@acme", tenant_id: str = "acme") -> str:
    """A validly signed token whose remaining lifetime the refresh window is measured against."""
    issued_at = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "tenant_id": tenant_id,
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=minutes),
        },
        TEST_SECRET,
        algorithm="HS256",
    )


def test_a_freshly_issued_token_is_not_refreshed():
    assert refreshed_token(create_token(Identity(sub="alice@acme", tenant_id="acme"))) is None


def test_a_token_outside_the_refresh_window_is_not_refreshed():
    outside = runtime().auth.refresh_within_minutes + 1
    assert refreshed_token(_token_expiring_in(outside)) is None


def test_a_token_inside_the_refresh_window_is_reissued():
    inside = runtime().auth.refresh_within_minutes / 2
    old = _token_expiring_in(inside, sub="bob@beta", tenant_id="beta")
    fresh = refreshed_token(old)
    assert fresh is not None
    assert fresh != old
    assert verify_token(fresh) == Identity(sub="bob@beta", tenant_id="beta")
    assert _claims(fresh)["exp"] > _claims(old)["exp"]


def test_a_reissued_token_carries_the_full_configured_lifetime():
    fresh = refreshed_token(_token_expiring_in(1))
    claims = _claims(fresh)
    assert claims["exp"] - claims["iat"] == runtime().auth.token_ttl_minutes * 60


def test_an_expired_token_is_never_refreshed():
    with pytest.raises(AuthError):
        refreshed_token(_token_expiring_in(-1))


def test_an_unsigned_token_is_never_refreshed():
    token = _unsigned_token({"sub": "alice@acme", "tenant_id": "beta", "iat": 0, "exp": 1 << 31})
    with pytest.raises(AuthError):
        refreshed_token(token)


def _claims(token: str) -> dict:
    """The token's claims, read back without re-checking expiry."""
    return jwt.decode(token, TEST_SECRET, algorithms=["HS256"], options={"verify_exp": False})


def test_tampered_signature_is_rejected():
    header, payload, signature = create_token(
        Identity(sub="alice@acme", tenant_id="acme")
    ).split(".")
    flipped = "B" if signature[0] != "B" else "C"
    with pytest.raises(AuthError):
        verify_token(f"{header}.{payload}.{flipped}{signature[1:]}")


def test_tampered_tenant_claim_is_rejected():
    header, _, signature = create_token(Identity(sub="alice@acme", tenant_id="acme")).split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"sub": "alice@acme", "tenant_id": "beta"}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        verify_token(f"{header}.{forged}.{signature}")


def test_alg_none_token_is_rejected():
    token = _unsigned_token({"sub": "alice@acme", "tenant_id": "beta", "iat": 0, "exp": 1 << 31})
    with pytest.raises(AuthError):
        verify_token(token)


def test_expired_token_is_rejected():
    issued_at = datetime.now(UTC) - timedelta(minutes=runtime().auth.token_ttl_minutes + 5)
    token = jwt.encode(
        {
            "sub": "alice@acme",
            "tenant_id": "acme",
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=runtime().auth.token_ttl_minutes),
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_token(token)


def test_token_signed_with_another_secret_is_rejected():
    token = jwt.encode(
        {
            "sub": "alice@acme",
            "tenant_id": "acme",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        OTHER_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_token(token)


def test_token_missing_tenant_claim_is_rejected():
    token = jwt.encode(
        {
            "sub": "alice@acme",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_token(token)


def test_missing_secret_raises(monkeypatch):
    monkeypatch.delenv(SECRET_ENV_VAR)
    with pytest.raises(AuthError, match=SECRET_ENV_VAR):
        jwt_secret()


@pytest.mark.parametrize("secret", ["", "short", "a1" * 16, "x" * 31])
def test_weak_secret_raises(monkeypatch, secret):
    monkeypatch.setenv(SECRET_ENV_VAR, secret)
    with pytest.raises(AuthError, match=SECRET_ENV_VAR):
        jwt_secret()


@pytest.mark.parametrize("secret", ["a1" * 32, "x" * 32])
def test_strong_secret_is_accepted(monkeypatch, secret):
    monkeypatch.setenv(SECRET_ENV_VAR, secret)
    assert jwt_secret() == secret
