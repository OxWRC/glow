"""Tests for the OIDC / dev-mode auth verifiers.

Uses a self-signed RSA keypair with discovery and JWKS lookups monkeypatched,
so no network call or real identity provider is needed.
"""

import base64
import datetime
import hashlib
import hmac
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from glow_api import auth as auth_module

TEST_ISSUER = "https://idp.example.com"
TEST_CLIENT_ID = "test-client-id"
TEST_JWKS_URI = f"{TEST_ISSUER}/jwks"


class _FakeSigningKey:
    """Mimics jwt.PyJWKClient's return value without a network call."""

    def __init__(self, key):
        self.key = key


class _FetchSpy:
    """Stands in for the discovery fetch; counts calls and can be made to fail."""

    def __init__(self, doc=None):
        self.calls = 0
        self.fail = False
        self.doc = doc or {"issuer": TEST_ISSUER, "jwks_uri": TEST_JWKS_URI}

    def __call__(self, url):
        self.calls += 1
        if self.fail:
            raise OSError("network down")
        return self.doc


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def fetch_spy(monkeypatch):
    spy = _FetchSpy()
    monkeypatch.setattr(auth_module, "_fetch_discovery", spy)
    return spy


@pytest.fixture
def oidc_verifier(rsa_keypair, fetch_spy, monkeypatch):
    _, public_key = rsa_keypair
    monkeypatch.setattr(
        jwt.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: _FakeSigningKey(public_key),
    )
    return auth_module._OidcVerifier(TEST_ISSUER, TEST_CLIENT_ID)


def _make_token(private_key, algorithm="RS256", **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": "oidc-sub-0001",
        "iss": TEST_ISSUER,
        "aud": TEST_CLIENT_ID,
        "email": "jdoe@example.com",
        "exp": now + datetime.timedelta(minutes=5),
        "iat": now,
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm=algorithm)


class TestOidcVerifier:
    def test_accepts_validly_signed_id_token(self, rsa_keypair, oidc_verifier):
        private_key, _ = rsa_keypair

        claims = oidc_verifier.decode(_make_token(private_key))

        assert claims["sub"] == "oidc-sub-0001"
        assert claims["email"] == "jdoe@example.com"

    def test_rejects_wrong_audience(self, rsa_keypair, oidc_verifier):
        token = _make_token(rsa_keypair[0], aud="someone-elses-client-id")
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    def test_rejects_wrong_issuer(self, rsa_keypair, oidc_verifier):
        token = _make_token(rsa_keypair[0], iss="https://evil.example.com")
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    def test_rejects_untrusted_key(self, oidc_verifier):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(_make_token(other))

    def test_rejects_expired_token(self, rsa_keypair, oidc_verifier):
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            minutes=5
        )
        token = _make_token(rsa_keypair[0], exp=past)
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    def test_rejects_hs256_token_signed_with_public_key(
        self, rsa_keypair, oidc_verifier
    ):
        from cryptography.hazmat.primitives import serialization

        _, public_key = rsa_keypair
        pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        # PyJWT refuses to encode this, so hand-build the classic
        # algorithm-confusion token: HS256 MAC keyed with the public PEM.
        def b64(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=")

        exp = int(
            (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5)
            ).timestamp()
        )
        claims = {"sub": "x", "iss": TEST_ISSUER, "aud": TEST_CLIENT_ID, "exp": exp}
        signing_input = (
            b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
            + b"."
            + b64(json.dumps(claims).encode())
        )
        sig = hmac.new(pem, signing_input, hashlib.sha256).digest()
        token = (signing_input + b"." + b64(sig)).decode()
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    def test_rejects_alg_none(self, oidc_verifier):
        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode(
            {
                "sub": "x",
                "iss": TEST_ISSUER,
                "aud": TEST_CLIENT_ID,
                "exp": now + datetime.timedelta(minutes=5),
            },
            None,
            algorithm="none",
        )
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    def test_discovery_is_lazy(self, fetch_spy):
        auth_module._OidcVerifier(TEST_ISSUER, TEST_CLIENT_ID)
        assert fetch_spy.calls == 0

    def test_discovery_failure_raises_pyjwt_error_and_retries(
        self, rsa_keypair, oidc_verifier, fetch_spy
    ):
        token = _make_token(rsa_keypair[0])
        fetch_spy.fail = True
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

        fetch_spy.fail = False
        assert oidc_verifier.decode(token)["sub"] == "oidc-sub-0001"

    def test_discovery_issuer_mismatch_rejected(
        self, rsa_keypair, oidc_verifier, fetch_spy
    ):
        fetch_spy.doc = {
            "issuer": "https://other.example.com",
            "jwks_uri": TEST_JWKS_URI,
        }
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(_make_token(rsa_keypair[0]))

    def test_discovery_url_strips_trailing_slash(self, rsa_keypair, monkeypatch):
        urls = []

        def spy(url):
            urls.append(url)
            return {"issuer": TEST_ISSUER + "/", "jwks_uri": TEST_JWKS_URI}

        monkeypatch.setattr(auth_module, "_fetch_discovery", spy)
        monkeypatch.setattr(
            jwt.PyJWKClient,
            "get_signing_key_from_jwt",
            lambda self, token: _FakeSigningKey(rsa_keypair[1]),
        )
        verifier = auth_module._OidcVerifier(TEST_ISSUER + "/", TEST_CLIENT_ID)
        verifier.decode(_make_token(rsa_keypair[0], iss=TEST_ISSUER + "/"))
        assert urls == [f"{TEST_ISSUER}/.well-known/openid-configuration"]


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
    def test_issuer_and_demo_mode_both_set_raises(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", True)
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", TEST_ISSUER)
        monkeypatch.setattr(auth_module.settings, "OIDC_CLIENT_ID", TEST_CLIENT_ID)

        with pytest.raises(RuntimeError):
            auth_module.settings.validate_auth_config()

    def test_issuer_without_client_id_raises(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", False)
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", TEST_ISSUER)
        monkeypatch.setattr(auth_module.settings, "OIDC_CLIENT_ID", None)

        with pytest.raises(RuntimeError):
            auth_module.settings.validate_auth_config()

    def test_issuer_must_be_https(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", False)
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", "http://idp.example")
        monkeypatch.setattr(auth_module.settings, "OIDC_CLIENT_ID", TEST_CLIENT_ID)

        with pytest.raises(RuntimeError):
            auth_module.settings.validate_auth_config()


class TestBuildVerifierFailsClosed:
    def test_no_issuer_and_demo_off_refuses_to_start(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", None)
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", False)

        with pytest.raises(RuntimeError, match="demo-mode auth"):
            auth_module._build_verifier()

    def test_no_issuer_and_demo_on_uses_dev_verifier(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", None)
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", True)

        assert isinstance(auth_module._build_verifier(), auth_module._DevVerifier)

    def test_issuer_with_client_id_builds_oidc_verifier(self, monkeypatch):
        monkeypatch.setattr(auth_module.settings, "OIDC_ISSUER", TEST_ISSUER)
        monkeypatch.setattr(auth_module.settings, "OIDC_CLIENT_ID", TEST_CLIENT_ID)
        monkeypatch.setattr(auth_module.settings, "DEMO_MODE", False)

        assert isinstance(auth_module._build_verifier(), auth_module._OidcVerifier)


class TestHardening:
    def test_token_without_exp_rejected(self, rsa_keypair, oidc_verifier):
        token = jwt.encode(
            {"sub": "x", "iss": TEST_ISSUER, "aud": TEST_CLIENT_ID},
            rsa_keypair[0],
            algorithm="RS256",
        )
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(token)

    @pytest.mark.parametrize(
        "doc",
        [
            ["not", "object"],
            "str",
            {"issuer": TEST_ISSUER, "jwks_uri": 5},
            {"issuer": TEST_ISSUER, "jwks_uri": None},
        ],
    )
    def test_malformed_discovery_doc_raises_pyjwt_error(
        self, rsa_keypair, oidc_verifier, fetch_spy, doc
    ):
        fetch_spy.doc = doc
        with pytest.raises(jwt.PyJWTError):
            oidc_verifier.decode(_make_token(rsa_keypair[0]))
