"""Tests for the Cognito/dev-mode auth verifier introduced in Task 2.

Scoped to the verifier itself (glow_api.auth._CognitoVerifier,
_DevVerifier, _build_verifier) - not the broader password-login-era test
suite, which is Task 9's job to rewrite.

Uses a self-signed RSA keypair and a monkeypatched JWKS client so no
network call / real AWS Cognito pool is needed for the Cognito-mode path.
"""

import datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from glow_api import auth as auth_module

TEST_POOL_ID = "eu-west-2_TESTPOOL"
TEST_REGION = "eu-west-2"
TEST_CLIENT_ID = "test-client-id"
TEST_ISSUER = f"https://cognito-idp.{TEST_REGION}.amazonaws.com/{TEST_POOL_ID}"


class _FakeSigningKey:
    """Mimics jwt.PyJWKClient's return value without a network call."""

    def __init__(self, key):
        self.key = key


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def cognito_verifier(rsa_keypair, monkeypatch):
    """A real _CognitoVerifier with its JWKS lookup monkeypatched to the test key."""
    _, public_key = rsa_keypair
    verifier = auth_module._CognitoVerifier(TEST_POOL_ID, TEST_CLIENT_ID, TEST_REGION)
    monkeypatch.setattr(
        verifier._jwks_client,
        "get_signing_key_from_jwt",
        lambda token: _FakeSigningKey(public_key),
    )
    return verifier


def _make_cognito_token(private_key, **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": "cognito-sub-0001",
        "iss": TEST_ISSUER,
        "aud": TEST_CLIENT_ID,
        "token_use": "id",
        "cognito:username": "jdoe",
        "email": "jdoe@example.com",
        "exp": now + datetime.timedelta(minutes=5),
        "iat": now,
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm="RS256")


class TestCognitoVerifier:
    def test_accepts_a_validly_signed_token(self, rsa_keypair, cognito_verifier):
        private_key, _ = rsa_keypair
        token = _make_cognito_token(private_key)

        claims = cognito_verifier.decode(token)

        assert claims["sub"] == "cognito-sub-0001"
        assert claims["cognito:username"] == "jdoe"

    def test_rejects_wrong_audience(self, rsa_keypair, cognito_verifier):
        private_key, _ = rsa_keypair
        token = _make_cognito_token(private_key, aud="someone-elses-client-id")

        with pytest.raises(jwt.PyJWTError):
            cognito_verifier.decode(token)

    def test_rejects_wrong_issuer(self, rsa_keypair, cognito_verifier):
        private_key, _ = rsa_keypair
        token = _make_cognito_token(
            private_key,
            iss=f"https://cognito-idp.{TEST_REGION}.amazonaws.com/eu-west-2_OTHERPOOL",
        )

        with pytest.raises(jwt.PyJWTError):
            cognito_verifier.decode(token)

    def test_rejects_a_token_signed_by_an_untrusted_key(self, cognito_verifier):
        # Simulates a forged token / an attacker-controlled key: the verifier's
        # (monkeypatched) JWKS lookup still returns the pool's real public key,
        # so a token signed by any other private key must fail signature checks.
        other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _make_cognito_token(other_private_key)

        with pytest.raises(jwt.PyJWTError):
            cognito_verifier.decode(token)


class TestDevVerifier:
    def test_accepts_a_token_from_create_access_token(self):
        verifier = auth_module._DevVerifier()
        token = auth_module.create_access_token({"sub": "some-user-sub"})

        claims = verifier.decode(token)

        assert claims["sub"] == "some-user-sub"
        assert claims["iss"] == auth_module.DEV_ISSUER

    def test_rejects_wrong_issuer(self):
        verifier = auth_module._DevVerifier()
        token = jwt.encode(
            {
                "sub": "x",
                "iss": "not-glow-dev",
                "exp": datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5),
            },
            auth_module.settings.SECRET_KEY,
            algorithm=auth_module.settings.ALGORITHM,
        )

        with pytest.raises(jwt.PyJWTError):
            verifier.decode(token)

    def test_rejects_expired_token(self):
        verifier = auth_module._DevVerifier()
        token = auth_module.create_access_token(
            {"sub": "some-user-sub"},
            expires_delta=datetime.timedelta(minutes=-5),
        )

        with pytest.raises(jwt.PyJWTError):
            verifier.decode(token)

    def test_rejects_wrong_signature(self):
        verifier = auth_module._DevVerifier()
        token = jwt.encode(
            {
                "sub": "x",
                "iss": auth_module.DEV_ISSUER,
                "exp": datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5),
            },
            "some-other-secret-key-entirely",
            algorithm=auth_module.settings.ALGORITHM,
        )

        with pytest.raises(jwt.PyJWTError):
            verifier.decode(token)


class TestValidateAuthConfig:
    """Direct unit coverage for settings.validate_auth_config() itself, not
    just via _build_verifier (see TestBuildVerifierFailsClosed above)."""

    def test_dev_bypass_and_cognito_pool_both_set_raises(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", True)
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", TEST_POOL_ID)

        with pytest.raises(RuntimeError):
            auth_module.settings.validate_auth_config()


class TestBuildVerifierFailsClosed:
    """Covers the review's two Critical findings: a missing/incomplete auth
    config must refuse to start, not silently fall back to something weaker.
    """

    def test_no_pool_and_dev_bypass_off_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", None)
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", False)

        with pytest.raises(RuntimeError):
            auth_module._build_verifier()

    def test_no_pool_and_dev_bypass_on_uses_dev_verifier(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", None)
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", True)

        assert isinstance(auth_module._build_verifier(), auth_module._DevVerifier)

    def test_pool_without_client_id_or_region_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", TEST_POOL_ID)
        monkeypatch.setattr(auth_module.settings, "COGNITO_CLIENT_ID", None)
        monkeypatch.setattr(auth_module.settings, "COGNITO_REGION", None)
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", False)

        with pytest.raises(RuntimeError):
            auth_module._build_verifier()

    def test_pool_with_client_id_and_region_builds_cognito_verifier(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", TEST_POOL_ID)
        monkeypatch.setattr(auth_module.settings, "COGNITO_CLIENT_ID", TEST_CLIENT_ID)
        monkeypatch.setattr(auth_module.settings, "COGNITO_REGION", TEST_REGION)
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", False)

        assert isinstance(auth_module._build_verifier(), auth_module._CognitoVerifier)

    def test_pool_and_dev_bypass_both_on_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "COGNITO_USER_POOL_ID", TEST_POOL_ID)
        monkeypatch.setattr(auth_module.settings, "COGNITO_CLIENT_ID", TEST_CLIENT_ID)
        monkeypatch.setattr(auth_module.settings, "COGNITO_REGION", TEST_REGION)
        monkeypatch.setattr(auth_module.settings, "DEV_AUTH_BYPASS", True)

        with pytest.raises(RuntimeError):
            auth_module._build_verifier()
