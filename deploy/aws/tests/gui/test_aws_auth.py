from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

import glow_deploy.gui.aws_auth as aws_auth
import glow_deploy.gui.secret_store as secret_store
from glow_deploy.errors import DeployError


class _FakeSsoOidcClient:
    def __init__(
        self, register_response=None, device_auth_response=None, token_responses=()
    ):
        self._register_response = register_response or {}
        self._device_auth_response = device_auth_response or {}
        self._token_responses = list(token_responses)

    def register_client(self, **kwargs):
        return self._register_response

    def start_device_authorization(self, **kwargs):
        return self._device_auth_response

    def create_token(self, **kwargs):
        response = self._token_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _device_auth(**overrides):
    defaults = dict(
        verification_uri="https://device.sso.example/",
        verification_uri_complete="https://device.sso.example/?user_code=ABCD-EFGH",
        user_code="ABCD-EFGH",
        device_code="device-code",
        interval=0,
        expires_in=600,
        client_id="client-id",
        client_secret="client-secret",
        region="eu-west-2",
    )
    defaults.update(overrides)
    return aws_auth.DeviceAuthorization(**defaults)


# ---------------------------------------------------------------------------
# Device authorization
# ---------------------------------------------------------------------------


def test_start_device_authorization_registers_client_and_returns_user_code(monkeypatch):
    fake_client = _FakeSsoOidcClient(
        register_response={"clientId": "client-id", "clientSecret": "client-secret"},
        device_auth_response={
            "verificationUri": "https://device.sso.example/",
            "verificationUriComplete": "https://device.sso.example/?user_code=ABCD-EFGH",
            "userCode": "ABCD-EFGH",
            "deviceCode": "device-code",
            "interval": 5,
            "expiresIn": 600,
        },
    )
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    result = aws_auth.start_device_authorization(
        "https://my-sso.awsapps.com/start", "eu-west-2"
    )

    assert result.user_code == "ABCD-EFGH"
    assert result.verification_uri_complete == (
        "https://device.sso.example/?user_code=ABCD-EFGH"
    )
    assert result.client_id == "client-id"
    assert result.client_secret == "client-secret"
    assert result.interval == 5


def test_start_device_authorization_raises_deploy_error_on_client_error(monkeypatch):
    class _FailingSsoOidcClient:
        def register_client(self, **kwargs):
            return {"clientId": "client-id", "clientSecret": "client-secret"}

        def start_device_authorization(self, **kwargs):
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidRequestException",
                        "Message": "bad start url",
                    }
                },
                "StartDeviceAuthorization",
            )

    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: _FailingSsoOidcClient(),
    )

    with pytest.raises(DeployError, match="InvalidRequestException"):
        aws_auth.start_device_authorization(
            "https://my-sso.awsapps.com/start/#/", "eu-west-2"
        )


def test_start_device_authorization_raises_deploy_error_on_botocore_error(monkeypatch):
    from botocore.exceptions import NoRegionError

    def _raise_no_region(service, region_name=None, config=None):
        raise NoRegionError()

    monkeypatch.setattr(aws_auth.boto3, "client", _raise_no_region)

    with pytest.raises(DeployError):
        aws_auth.start_device_authorization(
            "https://my-sso.awsapps.com/start/#/", "eu-west-2"
        )


def test_open_verification_url_tolerates_no_browser(monkeypatch):
    def _raise(url):
        raise RuntimeError("no browser available")

    monkeypatch.setattr(aws_auth.webbrowser, "open", _raise)

    aws_auth.open_verification_url(_device_auth())  # must not raise


# ---------------------------------------------------------------------------
# Token polling
# ---------------------------------------------------------------------------


def test_poll_for_token_retries_while_authorization_pending(monkeypatch):
    pending_error = ClientError(
        {"Error": {"Code": "AuthorizationPendingException"}}, "CreateToken"
    )
    fake_client = _FakeSsoOidcClient(
        token_responses=[
            pending_error,
            {"accessToken": "sso-access-token", "expiresIn": 3600},
        ]
    )
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )
    monkeypatch.setattr(aws_auth.time, "sleep", lambda seconds: None)

    token = aws_auth.poll_for_token(_device_auth())

    assert token.access_token == "sso-access-token"
    assert token.expires_in == 3600


def test_poll_for_token_raises_on_expired_code(monkeypatch):
    expired_error = ClientError(
        {"Error": {"Code": "ExpiredTokenException"}}, "CreateToken"
    )
    fake_client = _FakeSsoOidcClient(token_responses=[expired_error])
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    with pytest.raises(DeployError, match="expired"):
        aws_auth.poll_for_token(_device_auth())


def test_poll_for_token_raises_on_access_denied(monkeypatch):
    denied_error = ClientError(
        {"Error": {"Code": "AccessDeniedException"}}, "CreateToken"
    )
    fake_client = _FakeSsoOidcClient(token_responses=[denied_error])
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    with pytest.raises(DeployError, match="denied"):
        aws_auth.poll_for_token(_device_auth())


def test_poll_for_token_times_out(monkeypatch):
    fake_client = _FakeSsoOidcClient()
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    times = iter([0, 400])  # start, then first elapsed check already past timeout
    monkeypatch.setattr(aws_auth.time, "time", lambda: next(times))
    monkeypatch.setattr(aws_auth.time, "sleep", lambda seconds: None)

    with pytest.raises(DeployError, match="timed out"):
        aws_auth.poll_for_token(_device_auth(), timeout=300)


def test_poll_once_returns_none_while_pending(monkeypatch):
    pending_error = ClientError(
        {"Error": {"Code": "AuthorizationPendingException"}}, "CreateToken"
    )
    fake_client = _FakeSsoOidcClient(token_responses=[pending_error])
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    assert aws_auth.poll_once(_device_auth()) is None


def test_poll_once_returns_token_on_success(monkeypatch):
    fake_client = _FakeSsoOidcClient(
        token_responses=[{"accessToken": "sso-access-token", "expiresIn": 3600}]
    )
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    token = aws_auth.poll_once(_device_auth())

    assert token == aws_auth.SsoToken(access_token="sso-access-token", expires_in=3600)


def test_poll_once_raises_on_expired_code(monkeypatch):
    expired_error = ClientError(
        {"Error": {"Code": "ExpiredTokenException"}}, "CreateToken"
    )
    fake_client = _FakeSsoOidcClient(token_responses=[expired_error])
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    with pytest.raises(DeployError, match="expired"):
        aws_auth.poll_once(_device_auth())


# ---------------------------------------------------------------------------
# Account/role listing + session materialization
# ---------------------------------------------------------------------------


class _FakeAccountsPaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeAccountRolesPaginator:
    def __init__(self, roles_pages_by_account):
        self._roles_pages_by_account = roles_pages_by_account

    def paginate(self, accountId, **kwargs):
        return self._roles_pages_by_account[accountId]


class _FakeSsoClient:
    def __init__(
        self, accounts_pages=(), roles_pages_by_account=None, role_credentials=None
    ):
        self._accounts_pages = accounts_pages
        self._roles_pages_by_account = roles_pages_by_account or {}
        self._role_credentials = role_credentials

    def get_paginator(self, operation_name):
        if operation_name == "list_accounts":
            return _FakeAccountsPaginator(self._accounts_pages)
        if operation_name == "list_account_roles":
            return _FakeAccountRolesPaginator(self._roles_pages_by_account)
        raise AssertionError(operation_name)

    def get_role_credentials(self, **kwargs):
        return {"roleCredentials": self._role_credentials}


def test_list_accounts_and_roles_flattens_accounts_and_roles(monkeypatch):
    fake_client = _FakeSsoClient(
        accounts_pages=[
            {"accountList": [{"accountId": "111111111111", "accountName": "glow-prod"}]}
        ],
        roles_pages_by_account={
            "111111111111": [{"roleList": [{"roleName": "AdministratorAccess"}]}]
        },
    )
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    roles = aws_auth.list_accounts_and_roles(
        aws_auth.SsoToken(access_token="sso-access-token", expires_in=3600), "eu-west-2"
    )

    assert roles == [
        aws_auth.SsoAccountRole(
            account_id="111111111111",
            account_name="glow-prod",
            role_name="AdministratorAccess",
        )
    ]


def test_session_from_sso_role_exchanges_token_for_session_credentials(monkeypatch):
    fake_client = _FakeSsoClient(
        role_credentials={
            "accessKeyId": "AKIAEXAMPLE",
            "secretAccessKey": "secret",
            "sessionToken": "token",
        }
    )
    monkeypatch.setattr(
        aws_auth.boto3,
        "client",
        lambda service, region_name=None, config=None: fake_client,
    )

    session = aws_auth.session_from_sso_role(
        aws_auth.SsoToken(access_token="sso-access-token", expires_in=3600),
        aws_auth.SsoAccountRole(
            account_id="111111111111",
            account_name="glow-prod",
            role_name="AdministratorAccess",
        ),
        "eu-west-2",
    )

    frozen = session.get_credentials().get_frozen_credentials()
    assert frozen.access_key == "AKIAEXAMPLE"
    assert frozen.secret_key == "secret"
    assert frozen.token == "token"
    assert session.region_name == "eu-west-2"


# ---------------------------------------------------------------------------
# Manual access-key fallback
# ---------------------------------------------------------------------------


class _FakeManualSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.region_name = kwargs.get("region_name")

    def client(self, service_name):
        assert service_name == "sts"
        return SimpleNamespace(get_caller_identity=lambda: {"Account": "111111111111"})

    def get_credentials(self):
        frozen = SimpleNamespace(
            access_key=self.kwargs["aws_access_key_id"],
            secret_key=self.kwargs["aws_secret_access_key"],
            token=self.kwargs.get("aws_session_token"),
        )
        return SimpleNamespace(get_frozen_credentials=lambda: frozen)


def test_session_from_manual_credentials_validates_via_sts(monkeypatch):
    monkeypatch.setattr(aws_auth.boto3, "Session", _FakeManualSession)

    session = aws_auth.session_from_manual_credentials(
        "AKIAEXAMPLE", "secret", None, "eu-west-2"
    )

    assert session.kwargs["aws_access_key_id"] == "AKIAEXAMPLE"
    assert session.region_name == "eu-west-2"


def test_session_from_manual_credentials_raises_on_invalid_credentials(monkeypatch):
    class _RejectingSession:
        def __init__(self, **kwargs):
            pass

        def client(self, service_name):
            def get_caller_identity():
                raise ClientError(
                    {"Error": {"Code": "InvalidClientTokenId"}}, "GetCallerIdentity"
                )

            return SimpleNamespace(get_caller_identity=get_caller_identity)

    monkeypatch.setattr(aws_auth.boto3, "Session", _RejectingSession)

    with pytest.raises(DeployError, match="AWS credentials rejected"):
        aws_auth.session_from_manual_credentials("bad", "bad", None, "eu-west-2")


# ---------------------------------------------------------------------------
# secret_store bridge
# ---------------------------------------------------------------------------


def test_to_stored_credentials_extracts_frozen_credentials(monkeypatch):
    monkeypatch.setattr(aws_auth.boto3, "Session", _FakeManualSession)
    session = _FakeManualSession(
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="secret",
        aws_session_token="token",
        region_name="eu-west-2",
    )

    stored = aws_auth.to_stored_credentials(session, expiration="2026-01-01T00:00:00Z")

    assert stored == secret_store.StoredCredentials(
        access_key="AKIAEXAMPLE",
        secret_key="secret",
        session_token="token",
        expiration="2026-01-01T00:00:00Z",
        region="eu-west-2",
    )


def test_session_from_stored_credentials_rehydrates_a_session(monkeypatch):
    monkeypatch.setattr(aws_auth.boto3, "Session", _FakeManualSession)

    stored = secret_store.StoredCredentials(
        access_key="AKIAEXAMPLE",
        secret_key="secret",
        session_token="token",
        expiration=None,
        region="eu-west-2",
    )

    session = aws_auth.session_from_stored_credentials(stored)

    assert session.kwargs["aws_access_key_id"] == "AKIAEXAMPLE"
    assert session.kwargs["aws_session_token"] == "token"
    assert session.region_name == "eu-west-2"
