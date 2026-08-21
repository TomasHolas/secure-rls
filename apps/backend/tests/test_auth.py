"""Auth tests: PBKDF2 credentials and pinned-algorithm JWTs (issue #21, ADR 0009)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from auth import (
    _DEMO_USERS,
    SECRET_ENV_VAR,
    AuthError,
    Identity,
    create_token,
    jwt_secret,
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

    def segment(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}."


@pytest.mark.parametrize(("username", "password", "tenant_id"), DEMO_CREDENTIALS)
def test_correct_password_returns_identity(username, password, tenant_id):
    assert verify_password(username, password) == Identity(sub=username, tenant_id=tenant_id)


def test_wrong_password_returns_none():
    assert verify_password("alice@acme", "demo-beta") is None


def test_unknown_user_returns_none():
    assert verify_password("mallory@acme", "demo-acme") is None


def test_stored_hashes_match_adr_parameters():
    for tenant_id, stored in _DEMO_USERS.values():
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        assert algorithm == "pbkdf2_sha256"
        assert int(iterations) == 600_000
        assert len(bytes.fromhex(salt_hex)) == 16
        assert len(bytes.fromhex(digest_hex)) == 32
        assert tenant_id in {"acme", "beta", "gamma"}


def test_salts_are_unique_per_user():
    salts = {stored.split("$")[2] for _, stored in _DEMO_USERS.values()}
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
