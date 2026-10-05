"""AWS sign-in for the GUI: IAM Identity Center (SSO) device authorization,
plus a manual access-key fallback for accounts without Identity Center.

Both paths end the same way: a `boto3.Session` the rest of glow_deploy (core.py)
can drive, and a `secret_store.StoredCredentials` the caller can persist so the
user isn't asked to sign in again next launch.
"""

from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from glow_deploy.errors import DeployError
from glow_deploy.gui import secret_store

_CLIENT_NAME = "glow-deploy"
_CLIENT_TYPE = "public"
_SCOPES = ["sso:account:access"]

# sso-oidc's device-flow calls and sso's bearer-token calls need no IAM
# credentials at all — building these clients unsigned means construction
# doesn't touch the local credential chain, so a machine with a broken or
# unusual AWS credential setup (e.g. an SSO "login" provider that needs an
# optional `botocore[crt]` dependency this app doesn't bundle) can't crash a
# sign-in flow that was never going to use any of those credentials anyway.
_UNSIGNED = Config(signature_version=UNSIGNED)


@dataclass
class DeviceAuthorization:
    """What the caller shows the user: a code, and a URL to enter it at."""

    verification_uri: str
    verification_uri_complete: str
    user_code: str
    device_code: str
    interval: int
    expires_in: int
    client_id: str
    client_secret: str
    region: str


def start_device_authorization(start_url: str, region: str) -> DeviceAuthorization:
    """Kick off the SSO device-authorization grant.

    Returns immediately with a user code + verification URL for the caller to
    display; the user completes sign-in in their browser, then the caller
    polls `poll_for_token` until it succeeds.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        oidc = boto3.client("sso-oidc", region_name=region, config=_UNSIGNED)

        registration = oidc.register_client(
            clientName=_CLIENT_NAME,
            clientType=_CLIENT_TYPE,
            scopes=_SCOPES,
        )

        device_auth = oidc.start_device_authorization(
            clientId=registration["clientId"],
            clientSecret=registration["clientSecret"],
            startUrl=start_url,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        raise DeployError(
            f"Couldn't start SSO sign-in ({error_code or exc}). "
            "Check the start URL and that the region matches your "
            "Identity Center instance's home region."
        ) from exc
    except BotoCoreError as exc:
        raise DeployError(f"Couldn't start SSO sign-in: {exc}") from exc

    return DeviceAuthorization(
        verification_uri=device_auth["verificationUri"],
        verification_uri_complete=device_auth["verificationUriComplete"],
        user_code=device_auth["userCode"],
        device_code=device_auth["deviceCode"],
        interval=device_auth.get("interval", 5),
        expires_in=device_auth["expiresIn"],
        client_id=registration["clientId"],
        client_secret=registration["clientSecret"],
        region=region,
    )


def open_verification_url(device_authorization: DeviceAuthorization) -> None:
    """Open the system browser to complete sign-in.

    Best-effort: the GUI must also show the URL/code so the user can navigate
    there manually if this fails (e.g. no default browser configured).
    """
    try:
        webbrowser.open(device_authorization.verification_uri_complete)
    except Exception:
        pass


@dataclass
class SsoToken:
    access_token: str
    expires_in: int


# Codes CreateToken returns while the user hasn't finished signing in yet —
# not errors, just "keep polling".
_PENDING_ERROR_CODES = {"AuthorizationPendingException", "SlowDownException"}


def poll_once(device_authorization: DeviceAuthorization) -> SsoToken | None:
    """Make a single CreateToken attempt, for callers that poll via repeated
    HTTP requests (the GUI) rather than blocking one request for minutes.

    Returns None while the user hasn't finished signing in yet. Raises
    DeployError if the code expired or access was denied.
    """
    from botocore.exceptions import ClientError

    oidc = boto3.client(
        "sso-oidc", region_name=device_authorization.region, config=_UNSIGNED
    )
    try:
        token = oidc.create_token(
            clientId=device_authorization.client_id,
            clientSecret=device_authorization.client_secret,
            grantType="urn:ietf:params:oauth:grant-type:device_code",
            deviceCode=device_authorization.device_code,
        )
        return SsoToken(
            access_token=token["accessToken"], expires_in=token["expiresIn"]
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in _PENDING_ERROR_CODES:
            return None
        if error_code == "ExpiredTokenException":
            raise DeployError("SSO sign-in code expired; start over") from exc
        if error_code == "AccessDeniedException":
            raise DeployError("SSO sign-in was denied") from exc
        raise DeployError(f"SSO sign-in failed: {error_code or exc}") from exc


def poll_for_token(
    device_authorization: DeviceAuthorization, timeout: int = 300
) -> SsoToken:
    """Poll CreateToken until the user completes sign-in in their browser.

    Raises DeployError on an expired/denied code or on our own timeout.
    """
    from botocore.exceptions import ClientError

    oidc = boto3.client(
        "sso-oidc", region_name=device_authorization.region, config=_UNSIGNED
    )
    interval = device_authorization.interval
    start = time.time()

    while True:
        if time.time() - start > timeout:
            raise DeployError("timed out waiting for SSO sign-in")

        try:
            token = oidc.create_token(
                clientId=device_authorization.client_id,
                clientSecret=device_authorization.client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=device_authorization.device_code,
            )
            return SsoToken(
                access_token=token["accessToken"],
                expires_in=token["expiresIn"],
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "SlowDownException":
                interval += 5
            if error_code in _PENDING_ERROR_CODES:
                time.sleep(interval)
                continue
            if error_code == "ExpiredTokenException":
                raise DeployError("SSO sign-in code expired; start over") from exc
            if error_code == "AccessDeniedException":
                raise DeployError("SSO sign-in was denied") from exc
            raise DeployError(f"SSO sign-in failed: {error_code or exc}") from exc


@dataclass(frozen=True)
class SsoAccountRole:
    account_id: str
    account_name: str
    role_name: str


def list_accounts_and_roles(sso_token: SsoToken, region: str) -> list[SsoAccountRole]:
    """List the AWS accounts/roles this SSO identity can assume.

    Most orgs deploying Glow will have a single account/role; the GUI can
    auto-select when there's only one and only show a picker otherwise.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    sso = boto3.client("sso", region_name=region, config=_UNSIGNED)
    roles: list[SsoAccountRole] = []

    try:
        accounts_paginator = sso.get_paginator("list_accounts")
        for accounts_page in accounts_paginator.paginate(
            accessToken=sso_token.access_token
        ):
            for account in accounts_page.get("accountList", []):
                account_id = account["accountId"]
                account_name = account.get("accountName", account_id)

                roles_paginator = sso.get_paginator("list_account_roles")
                for roles_page in roles_paginator.paginate(
                    accessToken=sso_token.access_token, accountId=account_id
                ):
                    for role in roles_page.get("roleList", []):
                        roles.append(
                            SsoAccountRole(
                                account_id=account_id,
                                account_name=account_name,
                                role_name=role["roleName"],
                            )
                        )
    except (ClientError, BotoCoreError) as exc:
        raise DeployError(f"Couldn't list AWS accounts/roles: {exc}") from exc

    return roles


def session_from_sso_role(
    sso_token: SsoToken, account_role: SsoAccountRole, region: str
) -> boto3.Session:
    """Exchange an SSO access token + chosen account/role for a boto3.Session."""
    from botocore.exceptions import BotoCoreError, ClientError

    sso = boto3.client("sso", region_name=region, config=_UNSIGNED)
    try:
        role_credentials = sso.get_role_credentials(
            roleName=account_role.role_name,
            accountId=account_role.account_id,
            accessToken=sso_token.access_token,
        )["roleCredentials"]
    except (ClientError, BotoCoreError) as exc:
        raise DeployError(f"Couldn't get role credentials: {exc}") from exc

    return boto3.Session(
        aws_access_key_id=role_credentials["accessKeyId"],
        aws_secret_access_key=role_credentials["secretAccessKey"],
        aws_session_token=role_credentials["sessionToken"],
        region_name=region,
    )


def validate_session(session: boto3.Session) -> str:
    """Confirm the session's credentials actually work; return the account ID.

    Used on the manual-entry path so the GUI can show a friendly error at
    sign-in time rather than failing deep into a provision/update job.
    """
    from botocore.exceptions import ClientError

    sts = session.client("sts")
    try:
        return sts.get_caller_identity()["Account"]
    except ClientError as exc:
        raise DeployError(f"AWS credentials rejected: {exc}") from exc


def session_from_manual_credentials(
    access_key: str, secret_key: str, session_token: str | None, region: str
) -> boto3.Session:
    """Build and validate a session from manually entered access keys."""
    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        region_name=region,
    )
    validate_session(session)
    return session


def to_stored_credentials(
    session: boto3.Session, expiration: str | None = None
) -> secret_store.StoredCredentials:
    """Extract a session's frozen credentials for persistence via secret_store."""
    frozen = session.get_credentials().get_frozen_credentials()
    return secret_store.StoredCredentials(
        access_key=frozen.access_key,
        secret_key=frozen.secret_key,
        session_token=frozen.token,
        expiration=expiration,
        region=session.region_name,
    )


def session_from_stored_credentials(
    stored: secret_store.StoredCredentials,
) -> boto3.Session:
    """Rehydrate a session previously persisted via secret_store."""
    return boto3.Session(
        aws_access_key_id=stored.access_key,
        aws_secret_access_key=stored.secret_key,
        aws_session_token=stored.session_token,
        region_name=stored.region,
    )
