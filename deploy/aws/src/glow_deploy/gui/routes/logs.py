"""Status/logs routes for an existing deployment.

Health/git-ref come from the on-instance scripts the CLI's update flow
already reads via SSM (see core.get_runner_status). Container logs come from
the per-domain CloudWatch Logs group (see core.get_container_logs).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from glow_deploy import core
from glow_deploy.errors import DeployError
from glow_deploy.gui.ansi import ansi_to_html
from glow_deploy.gui.deps import find_deployment, require_session
from glow_deploy.gui.paths import log_file
from glow_deploy.gui.templating import templates

logger = logging.getLogger("glow_deploy.gui")

router = APIRouter()


@router.get("/deployments/{domain}/logs", response_class=HTMLResponse)
def logs(request: Request, domain: str, _session=Depends(require_session)):
    # Renders immediately; JS fetches the actual status from
    # /deployments/{domain}/logs/status so the SSM round-trip doesn't block
    # navigation to this page.
    deployment = find_deployment(request, domain)
    return templates.TemplateResponse(request, "deployment_logs.html", {"deployment": deployment})


@router.get("/deployments/{domain}/logs/status")
def logs_status(request: Request, domain: str, session=Depends(require_session)):
    deployment = find_deployment(request, domain)
    region = request.app.state.region

    try:
        status = core.get_runner_status(deployment["instance_id"], region, session)
        error = None
    except DeployError as exc:
        logger.error("Failed to fetch runner status for %s: %s", domain, exc, exc_info=exc)
        status = None
        error = (
            "Couldn't reach the server to check its status. "
            "It may still be starting up, or a service on it may be unhealthy. Try again shortly. "
            f"(Details logged to {log_file()})"
        )

    admin_credentials = None
    admin_credentials_error = None
    if status is not None:
        # Only worth an extra SSM round-trip once the one above proves SSM
        # is actually reachable on the instance.
        try:
            admin_credentials = core.get_admin_credentials(
                deployment["instance_id"], region, session
            )
        except DeployError as exc:
            logger.error(
                "Failed to fetch admin credentials for %s: %s", domain, exc, exc_info=exc
            )
            admin_credentials_error = (
                f"Couldn't fetch dashboard admin credentials. (Details logged to {log_file()})"
            )

    try:
        raw_containers = core.get_container_logs(deployment["instance_id"], domain, region, session)
        containers = {
            name: [ansi_to_html(line) for line in lines] for name, lines in raw_containers.items()
        }
        containers_error = None
    except DeployError as exc:
        logger.error("Failed to fetch container logs for %s: %s", domain, exc, exc_info=exc)
        containers = None
        containers_error = (
            "Couldn't fetch container logs. "
            f"(Details logged to {log_file()})"
        )

    return {
        "status": status,
        "error": error,
        "admin_credentials": admin_credentials,
        "admin_credentials_error": admin_credentials_error,
        "containers": containers,
        "containers_error": containers_error,
    }


@router.get("/deployments/{domain}/logs/containers/{container}/tail")
def container_log_tail(
    request: Request, domain: str, container: str, session=Depends(require_session)
):
    deployment = find_deployment(request, domain)
    region = request.app.state.region
    try:
        lines = core.get_container_log_tail(
            deployment["instance_id"], domain, container, region, session
        )
        return {"lines": [ansi_to_html(line) for line in lines], "error": None}
    except DeployError as exc:
        logger.error(
            "Failed to tail container %s for %s: %s", container, domain, exc, exc_info=exc
        )
        return {
            "lines": None,
            "error": f"Couldn't fetch logs for {container}. (Details logged to {log_file()})",
        }
