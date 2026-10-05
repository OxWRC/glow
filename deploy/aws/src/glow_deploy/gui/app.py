"""FastAPI application factory for the Glow deploy GUI.

`app.state` holds the process-lifetime signed-in session (single local user,
no multi-tenant concerns) plus the background job manager and any in-flight
SSO device-authorization attempt.
"""

from __future__ import annotations

import logging
import threading
import time

from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from glow_deploy.errors import DeployError
from glow_deploy.gui import secret_store, update_check, version
from glow_deploy.gui.aws_auth import session_from_stored_credentials
from glow_deploy.gui.jobs import JobManager
from glow_deploy.gui.paths import gui_dir
from glow_deploy.gui.routes import auth, deployments, heartbeat, jobs, logs
from glow_deploy.gui.templating import templates

_PROFILE = "default"

# boto3 error codes AWS uses for a signed-in session that has aged out —
# distinct from a role missing a permission, which is a different fix.
_EXPIRED_CREDENTIAL_CODES = {"ExpiredToken", "RequestExpired", "ExpiredTokenException"}

logger = logging.getLogger("glow_deploy.gui")


def create_app() -> FastAPI:
    app = FastAPI(title="Glow Deploy")
    app.mount(
        "/static", StaticFiles(directory=str(gui_dir() / "static")), name="static"
    )

    app.state.region = "eu-west-2"
    app.state.job_manager = JobManager()
    app.state.pending_device_auth = None
    app.state.sso_token = None
    app.state.current_version = version.CURRENT_VERSION
    app.state.latest_release = None
    app.state.release_tags_cache = None
    # Set at startup (not left at 0) so the watcher thread doesn't see a stale
    # timestamp and quit before the browser tab's first heartbeat lands.
    app.state.last_heartbeat = time.time()

    stored = secret_store.load_credentials(_PROFILE)
    app.state.session = session_from_stored_credentials(stored) if stored else None
    if stored:
        app.state.region = stored.region

    app.include_router(auth.router)
    app.include_router(deployments.router)
    app.include_router(heartbeat.router)
    app.include_router(jobs.router)
    app.include_router(logs.router)

    app.add_exception_handler(ClientError, _handle_client_error)
    app.add_exception_handler(DeployError, _handle_deploy_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)

    # Skipped entirely for source/dev runs ("dev" has nothing to compare
    # against) — see version.py.
    if app.state.current_version != "dev":
        threading.Thread(target=_check_for_update, args=(app,), daemon=True).start()

    return app


def _render_error(
    request: Request,
    message: str,
    *,
    signin_again: bool = False,
    status_code: int = 500,
):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"message": message, "signin_again": signin_again},
        status_code=status_code,
    )


def _handle_client_error(request: Request, exc: ClientError):
    """An AWS API call failed — most commonly the signed-in role lacking a
    permission the tool needs (e.g. EC2 describe/SSM), which boto3 raises as
    a bare ClientError with no route-level handling to turn it into a
    friendly message. A separate, common case is credentials that have aged
    out (RequestExpired/ExpiredToken*) — that's not a permissions problem,
    it's a "sign in again" prompt."""
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    logger.error(
        "AWS request failed on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
    )
    if code in _EXPIRED_CREDENTIAL_CODES:
        request.app.state.session = None
        secret_store.delete_credentials(_PROFILE)
        return _render_error(
            request, "Your AWS sign-in has expired.", signin_again=True, status_code=401
        )
    return _render_error(
        request,
        f"AWS rejected this request ({code}). The signed-in role may be missing "
        "a required permission — check with whoever manages your AWS account.",
    )


def _handle_deploy_error(request: Request, exc: DeployError):
    logger.error(
        "Deploy error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc
    )
    return _render_error(request, str(exc))


def _handle_unexpected_error(request: Request, exc: Exception):
    logger.error(
        "Unhandled error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
    )
    return _render_error(
        request, "Something went wrong. Please try again or restart the app."
    )


def _check_for_update(app: FastAPI) -> None:
    tag = update_check.latest_release_tag(app.state.current_version)
    if tag:
        app.state.latest_release = tag
