"""Sign-in routes: AWS SSO device-authorization flow and manual access-key fallback.

The SSO device flow is inherently slow (the user has to switch to a browser
and sign in) so the GUI polls /signin/sso/poll with repeated short requests
via `aws_auth.poll_once` rather than blocking one request for minutes on
`aws_auth.poll_for_token`.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from glow_deploy.errors import DeployError
from glow_deploy.gui import aws_auth, secret_store
from glow_deploy.gui.templating import templates

router = APIRouter()

# Single local user, single stored profile — no multi-account switching in v1.
_PROFILE = "default"


@router.get("/")
def root(request: Request):
    dest = "/deployments" if request.app.state.session else "/signin"
    return RedirectResponse(dest, status_code=303)


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request):
    return templates.TemplateResponse(request, "signin.html", {"error": None})


@router.post("/signin/sso/start")
def sso_start(
    request: Request, start_url: str = Form(...), region: str = Form("eu-west-2")
):
    try:
        device_auth = aws_auth.start_device_authorization(start_url, region)
    except DeployError as exc:
        return templates.TemplateResponse(request, "signin.html", {"error": str(exc)})

    aws_auth.open_verification_url(device_auth)
    request.app.state.pending_device_auth = device_auth
    return RedirectResponse("/signin/sso/poll", status_code=303)


@router.get("/signin/sso/poll", response_class=HTMLResponse)
def sso_poll(request: Request):
    device_auth = request.app.state.pending_device_auth
    if device_auth is None:
        return RedirectResponse("/signin", status_code=303)

    try:
        token = aws_auth.poll_once(device_auth)
    except DeployError as exc:
        request.app.state.pending_device_auth = None
        return templates.TemplateResponse(request, "signin.html", {"error": str(exc)})

    if token is None:
        return templates.TemplateResponse(
            request,
            "sso_poll.html",
            {"device_auth": device_auth, "accounts": None, "error": None},
        )

    request.app.state.sso_token = token
    try:
        accounts = aws_auth.list_accounts_and_roles(token, device_auth.region)
    except DeployError as exc:
        request.app.state.pending_device_auth = None
        return templates.TemplateResponse(request, "signin.html", {"error": str(exc)})

    if len(accounts) == 1:
        try:
            return _complete_sso_signin(request, accounts[0], device_auth.region)
        except DeployError as exc:
            request.app.state.pending_device_auth = None
            return templates.TemplateResponse(
                request, "signin.html", {"error": str(exc)}
            )

    return templates.TemplateResponse(
        request,
        "sso_poll.html",
        {"device_auth": device_auth, "accounts": accounts, "error": None},
    )


@router.post("/signin/sso/select")
def sso_select(
    request: Request,
    account_id: str = Form(...),
    role_name: str = Form(...),
    account_name: str = Form(""),
):
    token = request.app.state.sso_token
    device_auth = request.app.state.pending_device_auth
    if token is None or device_auth is None:
        return RedirectResponse("/signin", status_code=303)

    account_role = aws_auth.SsoAccountRole(
        account_id=account_id, account_name=account_name, role_name=role_name
    )
    try:
        return _complete_sso_signin(request, account_role, device_auth.region)
    except DeployError as exc:
        request.app.state.pending_device_auth = None
        return templates.TemplateResponse(request, "signin.html", {"error": str(exc)})


def _complete_sso_signin(
    request: Request, account_role: aws_auth.SsoAccountRole, region: str
) -> RedirectResponse:
    token = request.app.state.sso_token
    session = aws_auth.session_from_sso_role(token, account_role, region)
    request.app.state.session = session
    request.app.state.region = region
    request.app.state.pending_device_auth = None
    request.app.state.sso_token = None
    secret_store.save_credentials(_PROFILE, aws_auth.to_stored_credentials(session))
    return RedirectResponse("/deployments", status_code=303)


@router.post("/signin/manual")
def manual_signin(
    request: Request,
    access_key: str = Form(...),
    secret_key: str = Form(...),
    session_token: str = Form(""),
    region: str = Form("eu-west-2"),
):
    try:
        session = aws_auth.session_from_manual_credentials(
            access_key, secret_key, session_token or None, region
        )
    except DeployError as exc:
        return templates.TemplateResponse(request, "signin.html", {"error": str(exc)})

    request.app.state.session = session
    request.app.state.region = region
    secret_store.save_credentials(_PROFILE, aws_auth.to_stored_credentials(session))
    return RedirectResponse("/deployments", status_code=303)


@router.post("/signin/out")
def sign_out(request: Request):
    request.app.state.session = None
    secret_store.delete_credentials(_PROFILE)
    return RedirectResponse("/signin", status_code=303)
