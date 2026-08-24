"""Deployment listing, creation (plan/apply), and update (plan/apply) routes.

Both the new-deployment and update flows follow the same shape: a "plan" POST
resolves the git ref and runs a dry-run job (terraform plan output ends up in
the job's progress lines), then a "Confirm & Deploy" form on that job's page
POSTs to the matching "apply" route with the resolved fields replayed as
hidden inputs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from glow_deploy import core, github_api, versions
from glow_deploy.errors import DeployError
from glow_deploy.gui import deps
from glow_deploy.gui.deps import find_deployment, require_session
from glow_deploy.gui.templating import templates

router = APIRouter()


def _default_git_ref(request: Request) -> str:
    tags = deps.get_cached_release_tags(request)
    return versions.highest(tags, core.CORE_TAG_PREFIX) or "main"


def _sorted_available_versions(request: Request) -> list[str]:
    tags = deps.get_cached_release_tags(request)
    return sorted(tags, key=lambda tag: versions.parse(tag, core.CORE_TAG_PREFIX), reverse=True)


def _compute_version_info(domain: str, available: list[str]) -> dict:
    """Three states for "what version is this deployment on":
    - unreachable: the live API didn't respond (a bigger problem than a stale version)
    - custom: it responded, but isn't running a clean vX.Y.Z tag (advanced/branch track)
    - tracked: it's on a clean tag — update_to/upgrade_to may be non-None
    """
    deployed_version = core.get_deployed_version(domain)
    if deployed_version is None:
        return {"state": "unreachable", "status": None, "deployed_version": None}
    status = versions.classify(deployed_version, available, core.CORE_TAG_PREFIX)
    return {
        "state": "tracked" if status else "custom",
        "status": status,
        "deployed_version": deployed_version,
    }


@router.get("/deployments", response_class=HTMLResponse)
def home(request: Request, session=Depends(require_session)):
    deployments = core.list_deployments(session, request.app.state.region)
    running_ids = [d["instance_id"] for d in deployments if d["state"] == "running"]
    cpu = core.get_cpu_utilization(running_ids, request.app.state.region, session)
    available = deps.get_cached_release_tags(request)
    for deployment in deployments:
        deployment["cpu_percent"] = cpu.get(deployment["instance_id"])
        deployment["version_info"] = _compute_version_info(deployment["domain"], available)
    return templates.TemplateResponse(request, "home.html", {"deployments": deployments}
    )


@router.get("/deployments/new", response_class=HTMLResponse)
def new_deployment_form(request: Request, session=Depends(require_session)):
    return templates.TemplateResponse(request, "new_deployment.html", {"error": None,
            "available_versions": _sorted_available_versions(request),
            "defaults": {
                "git_repo_url": core.DEFAULT_GIT_REPO_URL,
                "git_ref": _default_git_ref(request),
                "aws_region": request.app.state.region,
                "app_name": "glow-core",
                "runner_instance_type": "t3.medium",
                "runner_root_volume_size_gb": 100,
            },
        },
    )


@router.get("/deployments/check-domain")
def check_domain(request: Request, domain: str, session=Depends(require_session)):
    """Report whether ``domain`` can have its certificate/DNS auto-managed.

    Polled by the new-deployment form's JS to decide whether to show the
    "Certificate ARN" field or a "this domain can be configured
    automatically" notice instead.
    """
    zone_id = None
    if domain:
        zone_id = core.find_hosted_zone_id(
            domain, request.app.state.region, session
        )
    return JSONResponse({"auto": zone_id is not None})


@router.post("/deployments/new/plan", response_class=HTMLResponse)
def new_deployment_plan(
    request: Request,
    session=Depends(require_session),
    domain: str = Form(...),
    certificate_arn: str = Form(""),
    git_repo_url: str = Form(core.DEFAULT_GIT_REPO_URL),
    git_ref: str = Form(""),
    git_ref_override: str = Form(""),
    aws_region: str = Form(...),
    app_name: str = Form("glow-core"),
    runner_instance_type: str = Form("t3.medium"),
    runner_root_volume_size_gb: int = Form(100),
    force_rebuild_ami: bool = Form(False),
    restore_from_snapshot_id: str = Form(""),
):
    resolved_ref = git_ref_override.strip() or git_ref or _default_git_ref(request)
    try:
        git_commit = github_api.resolve_git_commit_via_github(git_repo_url, resolved_ref)
    except DeployError as exc:
        return templates.TemplateResponse(request, "new_deployment.html", {"error": str(exc),
                "available_versions": _sorted_available_versions(request),
                "defaults": {
                    "git_repo_url": git_repo_url,
                    "git_ref": resolved_ref,
                    "aws_region": aws_region,
                    "app_name": app_name,
                    "runner_instance_type": runner_instance_type,
                    "runner_root_volume_size_gb": runner_root_volume_size_gb,
                },
            },
        )

    config_fields = dict(
        domain_name=domain,
        certificate_arn=certificate_arn,
        git_repo_url=git_repo_url,
        git_ref=resolved_ref,
        git_commit=git_commit,
        aws_region=aws_region,
        app_name=app_name,
        runner_instance_type=runner_instance_type,
        runner_root_volume_size_gb=runner_root_volume_size_gb,
        force_rebuild_ami=force_rebuild_ami,
        restore_from_snapshot_id=restore_from_snapshot_id,
    )
    config = core.Config(session=session, dry_run=True, **config_fields)
    job_id = request.app.state.job_manager.submit(
        lambda: core.provision(config),
        meta={
            "kind": "provision_plan",
            "domain": domain,
            "apply_action": "/deployments/new/apply",
            "config": config_fields,
        },
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/deployments/new/apply", response_class=HTMLResponse)
def new_deployment_apply(
    request: Request,
    session=Depends(require_session),
    domain_name: str = Form(...),
    certificate_arn: str = Form(""),
    git_repo_url: str = Form(...),
    git_ref: str = Form(...),
    git_commit: str = Form(...),
    aws_region: str = Form(...),
    app_name: str = Form(...),
    runner_instance_type: str = Form(...),
    runner_root_volume_size_gb: int = Form(...),
    force_rebuild_ami: bool = Form(False),
    restore_from_snapshot_id: str = Form(""),
):
    config = core.Config(
        session=session,
        dry_run=False,
        domain_name=domain_name,
        certificate_arn=certificate_arn,
        git_repo_url=git_repo_url,
        git_ref=git_ref,
        git_commit=git_commit,
        aws_region=aws_region,
        app_name=app_name,
        runner_instance_type=runner_instance_type,
        runner_root_volume_size_gb=runner_root_volume_size_gb,
        force_rebuild_ami=force_rebuild_ami,
        restore_from_snapshot_id=restore_from_snapshot_id,
    )
    job_id = request.app.state.job_manager.submit(
        lambda: core.provision(config),
        meta={"kind": "provision_apply", "domain": domain_name},
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.get("/deployments/{domain}", response_class=HTMLResponse)
def deployment_detail(request: Request, domain: str, session=Depends(require_session)):
    deployment = find_deployment(request, domain)
    available = deps.get_cached_release_tags(request)
    return templates.TemplateResponse(request, "deployment_detail.html", {
            "deployment": deployment,
            "error": None,
            "version_info": _compute_version_info(domain, available),
            "available_versions": sorted(
                available, key=lambda tag: versions.parse(tag, core.CORE_TAG_PREFIX), reverse=True
            ),
            "default_git_ref": _default_git_ref(request),
            "default_git_repo_url": core.DEFAULT_GIT_REPO_URL,
            "snapshots": core.list_snapshots(request.app.state.region, session, domain=domain),
        },
    )


@router.post("/deployments/{domain}/snapshots/{snapshot_id}/delete", response_class=HTMLResponse)
def delete_deployment_snapshot(
    request: Request, domain: str, snapshot_id: str, session=Depends(require_session)
):
    core.delete_snapshot(snapshot_id, request.app.state.region, session)
    return RedirectResponse(f"/deployments/{domain}", status_code=303)


@router.post("/deployments/{domain}/update/plan", response_class=HTMLResponse)
def update_plan(
    request: Request,
    domain: str,
    session=Depends(require_session),
    git_repo_url: str = Form(core.DEFAULT_GIT_REPO_URL),
    git_ref: str = Form(""),
    git_ref_override: str = Form(""),
):
    deployment = find_deployment(request, domain)
    resolved_ref = git_ref_override.strip() or git_ref or _default_git_ref(request)
    try:
        git_commit = github_api.resolve_git_commit_via_github(git_repo_url, resolved_ref)
    except DeployError as exc:
        available = deps.get_cached_release_tags(request)
        return templates.TemplateResponse(request, "deployment_detail.html", {
                "deployment": deployment,
                "error": str(exc),
                "version_info": _compute_version_info(domain, available),
                "available_versions": sorted(
                    available, key=lambda tag: versions.parse(tag, core.CORE_TAG_PREFIX), reverse=True
                ),
                "default_git_ref": _default_git_ref(request),
                "default_git_repo_url": core.DEFAULT_GIT_REPO_URL,
            },
        )

    config_fields = dict(
        domain_name=domain,
        git_repo_url=git_repo_url,
        git_ref=resolved_ref,
        git_commit=git_commit,
        aws_region=request.app.state.region,
        app_name="glow-core",
        runner_instance_type="",
        runner_root_volume_size_gb=0,
        force_rebuild_ami=False,
    )
    config = core.Config(session=session, dry_run=True, **config_fields)
    currently_running = core.get_deployed_version(domain)
    job_id = request.app.state.job_manager.submit(
        lambda: core.update(config),
        meta={
            "kind": "update_plan",
            "domain": domain,
            "apply_action": f"/deployments/{domain}/update/apply",
            "config": config_fields,
            "currently_running": currently_running,
            "deploying": resolved_ref,
        },
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/deployments/{domain}/update/apply", response_class=HTMLResponse)
def update_apply(
    request: Request,
    domain: str,
    session=Depends(require_session),
    domain_name: str = Form(...),
    git_repo_url: str = Form(...),
    git_ref: str = Form(...),
    git_commit: str = Form(...),
    aws_region: str = Form(...),
    app_name: str = Form(...),
    runner_instance_type: str = Form(""),
    runner_root_volume_size_gb: int = Form(0),
    force_rebuild_ami: bool = Form(False),
):
    config = core.Config(
        session=session,
        dry_run=False,
        domain_name=domain_name,
        git_repo_url=git_repo_url,
        git_ref=git_ref,
        git_commit=git_commit,
        aws_region=aws_region,
        app_name=app_name,
        runner_instance_type=runner_instance_type,
        runner_root_volume_size_gb=runner_root_volume_size_gb,
        force_rebuild_ami=force_rebuild_ami,
    )
    job_id = request.app.state.job_manager.submit(
        lambda: core.update(config),
        meta={"kind": "update_apply", "domain": domain_name},
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.post("/deployments/{domain}/destroy", response_class=HTMLResponse)
def destroy(request: Request, domain: str, session=Depends(require_session)):
    deployment = find_deployment(request, domain)
    config = core.Config(
        session=session,
        dry_run=False,
        domain_name=domain,
        git_repo_url=core.DEFAULT_GIT_REPO_URL,
        git_ref=deployment.get("git_ref") or "",
        git_commit=deployment.get("git_commit") or "",
        aws_region=request.app.state.region,
        app_name="glow-core",
        runner_instance_type="",
        runner_root_volume_size_gb=0,
        force_rebuild_ami=False,
    )
    job_id = request.app.state.job_manager.submit(
        lambda: core.destroy(config),
        meta={"kind": "destroy_apply", "domain": domain},
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.get("/snapshots", response_class=HTMLResponse)
def all_snapshots(request: Request, session=Depends(require_session)):
    snapshots = core.list_snapshots(request.app.state.region, session)
    return templates.TemplateResponse(request, "snapshots.html", {"snapshots": snapshots})


@router.post("/snapshots/{snapshot_id}/delete", response_class=HTMLResponse)
def delete_global_snapshot(request: Request, snapshot_id: str, session=Depends(require_session)):
    core.delete_snapshot(snapshot_id, request.app.state.region, session)
    return RedirectResponse("/snapshots", status_code=303)
