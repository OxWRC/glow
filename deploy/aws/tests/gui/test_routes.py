"""FastAPI route tests for the GUI backend (Phase 3): auth wiring, session
gating, and the plan/apply job flow for new deployments and updates.

Core AWS-touching functions (core.list_deployments/provision/update, the
SSO/manual sign-in helpers, GitHub ref resolution) are monkeypatched — these
tests exercise route/job wiring, not real AWS or network calls.
"""

from __future__ import annotations

import time

import pytest
from botocore.exceptions import ClientError
from starlette.testclient import TestClient

from glow_deploy import core, github_api
from glow_deploy.errors import DeployError
from glow_deploy.gui import aws_auth, deps, secret_store
from glow_deploy.gui.app import create_app


@pytest.fixture(autouse=True)
def _no_network_version_checks(monkeypatch):
    """The release-track routes call out to GitHub (cached tag list) and to
    each deployment's own live API (current version) — keep these tests
    deterministic and offline by default; individual tests override either
    with their own monkeypatch as needed."""
    monkeypatch.setattr(deps, "get_cached_release_tags", lambda request: [])
    monkeypatch.setattr(core, "get_deployed_version", lambda domain_name, timeout=5.0: None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(secret_store, "load_credentials", lambda profile: None)
    monkeypatch.setattr(secret_store, "save_credentials", lambda profile, creds: None)
    monkeypatch.setattr(secret_store, "delete_credentials", lambda profile: None)
    app = create_app()
    return TestClient(app)


def _sign_in(client: TestClient) -> None:
    client.app.state.session = object()
    client.app.state.region = "eu-west-2"


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["status"] not in ("pending", "running"):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Session gating
# ---------------------------------------------------------------------------


def test_signin_page_renders_without_a_session(client):
    response = client.get("/signin")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_protected_routes_redirect_to_signin_when_signed_out(client):
    response = client.get("/deployments", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_root_redirects_to_signin_when_signed_out(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_root_redirects_to_deployments_when_signed_in(client):
    _sign_in(client)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/deployments"


def test_heartbeat_updates_last_heartbeat_timestamp(client):
    client.app.state.last_heartbeat = 0.0
    response = client.post("/heartbeat")
    assert response.status_code == 204
    assert client.app.state.last_heartbeat > 0.0


# ---------------------------------------------------------------------------
# Manual sign-in
# ---------------------------------------------------------------------------


def test_manual_signin_sets_session_and_redirects_home(client, monkeypatch):
    sentinel_session = object()
    monkeypatch.setattr(
        aws_auth, "session_from_manual_credentials", lambda *a, **k: sentinel_session
    )
    monkeypatch.setattr(aws_auth, "to_stored_credentials", lambda session, **k: object())

    response = client.post(
        "/signin/manual",
        data={"access_key": "AKIA", "secret_key": "secret", "region": "eu-west-2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/deployments"
    assert client.app.state.session is sentinel_session


def test_manual_signin_shows_error_on_rejected_credentials(client, monkeypatch):
    monkeypatch.setattr(
        aws_auth,
        "session_from_manual_credentials",
        lambda *a, **k: (_ for _ in ()).throw(DeployError("AWS credentials rejected")),
    )

    response = client.post(
        "/signin/manual",
        data={"access_key": "bad", "secret_key": "bad", "region": "eu-west-2"},
    )

    assert response.status_code == 200
    assert "AWS credentials rejected" in response.text
    assert client.app.state.session is None


# ---------------------------------------------------------------------------
# SSO device-authorization flow
# ---------------------------------------------------------------------------


def _start_sso(client: TestClient, device_auth) -> None:
    client.app.state.pending_device_auth = device_auth
    client.app.state.sso_token = aws_auth.SsoToken(access_token="token", expires_in=3600)


def test_sso_start_shows_error_when_device_authorization_fails(client, monkeypatch):
    monkeypatch.setattr(
        aws_auth,
        "start_device_authorization",
        lambda *a, **k: (_ for _ in ()).throw(DeployError("start SSO sign-in failed")),
    )

    response = client.post(
        "/signin/sso/start",
        data={"start_url": "https://ox-lza-master.awsapps.com/start/#/", "region": "eu-west-2"},
    )

    assert response.status_code == 200
    assert "start SSO sign-in failed" in response.text


def test_sso_poll_shows_error_when_listing_accounts_fails(client, monkeypatch):
    device_auth = aws_auth.DeviceAuthorization(
        verification_uri="https://device.sso.example/",
        verification_uri_complete="https://device.sso.example/?user_code=ABCD",
        user_code="ABCD",
        device_code="device-code",
        interval=0,
        expires_in=600,
        client_id="client-id",
        client_secret="client-secret",
        region="eu-west-2",
    )
    client.app.state.pending_device_auth = device_auth
    monkeypatch.setattr(
        aws_auth, "poll_once", lambda *a, **k: aws_auth.SsoToken("token", 3600)
    )
    monkeypatch.setattr(
        aws_auth,
        "list_accounts_and_roles",
        lambda *a, **k: (_ for _ in ()).throw(DeployError("could not list AWS accounts")),
    )

    response = client.get("/signin/sso/poll")

    assert response.status_code == 200
    assert "could not list AWS accounts" in response.text
    assert client.app.state.pending_device_auth is None


def test_sso_select_shows_error_when_role_exchange_fails(client, monkeypatch):
    device_auth = aws_auth.DeviceAuthorization(
        verification_uri="https://device.sso.example/",
        verification_uri_complete="https://device.sso.example/?user_code=ABCD",
        user_code="ABCD",
        device_code="device-code",
        interval=0,
        expires_in=600,
        client_id="client-id",
        client_secret="client-secret",
        region="eu-west-2",
    )
    _start_sso(client, device_auth)
    monkeypatch.setattr(
        aws_auth,
        "session_from_sso_role",
        lambda *a, **k: (_ for _ in ()).throw(DeployError("could not get role credentials")),
    )

    response = client.post(
        "/signin/sso/select",
        data={"account_id": "111111111111", "role_name": "AdministratorAccess"},
    )

    assert response.status_code == 200
    assert "could not get role credentials" in response.text
    assert client.app.state.session is None


# ---------------------------------------------------------------------------
# Home / deployment listing
# ---------------------------------------------------------------------------


def test_home_lists_deployments(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core,
        "list_deployments",
        lambda session, region: [
            {
                "instance_id": "i-123",
                "state": "running",
                "domain": "example.com",
                "git_ref": "main",
                "git_commit": "deadbeef" * 5,
                "launch_time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(core, "get_cpu_utilization", lambda ids, region, session: {})

    response = client.get("/deployments")

    assert response.status_code == 200
    assert "example.com" in response.text


def test_expired_credentials_prompt_signin_again(client, monkeypatch):
    _sign_in(client)

    def _raise(session, region):
        raise ClientError({"Error": {"Code": "RequestExpired"}}, "DescribeInstances")

    monkeypatch.setattr(core, "list_deployments", _raise)

    response = client.get("/deployments")

    assert response.status_code == 401
    assert "expired" in response.text.lower()
    assert 'href="/signin"' in response.text
    assert client.app.state.session is None


# ---------------------------------------------------------------------------
# New deployment: plan -> apply
# ---------------------------------------------------------------------------


def test_new_deployment_plan_then_apply_provisions(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        github_api, "resolve_git_commit_via_github", lambda repo_url, ref: "c" * 40
    )
    provision_calls = []
    monkeypatch.setattr(
        core, "provision", lambda config: provision_calls.append(config) or core.write_line("done")
    )

    plan_response = client.post(
        "/deployments/new/plan",
        data={
            "domain": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "main",
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
            "runner_instance_type": "t3.medium",
            "runner_root_volume_size_gb": "100",
        },
        follow_redirects=False,
    )
    assert plan_response.status_code == 303
    plan_job_id = plan_response.headers["location"].removeprefix("/jobs/")

    plan_status = _wait_for_job(client, plan_job_id)
    assert plan_status["status"] == "succeeded"
    assert len(provision_calls) == 1
    assert provision_calls[0].dry_run is True

    job_page = client.get(f"/jobs/{plan_job_id}")
    assert "Confirm" in job_page.text
    assert 'action="/deployments/new/apply"' in job_page.text

    apply_response = client.post(
        "/deployments/new/apply",
        data={
            "domain_name": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "main",
            "git_commit": "c" * 40,
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
            "runner_instance_type": "t3.medium",
            "runner_root_volume_size_gb": "100",
        },
        follow_redirects=False,
    )
    assert apply_response.status_code == 303
    apply_job_id = apply_response.headers["location"].removeprefix("/jobs/")

    apply_status = _wait_for_job(client, apply_job_id)
    assert apply_status["status"] == "succeeded"
    assert len(provision_calls) == 2
    assert provision_calls[1].dry_run is False
    assert provision_calls[1].git_commit == "c" * 40


def test_new_deployment_plan_surfaces_git_ref_errors(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        github_api,
        "resolve_git_commit_via_github",
        lambda repo_url, ref: (_ for _ in ()).throw(DeployError("ref not found")),
    )

    response = client.post(
        "/deployments/new/plan",
        data={
            "domain": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "nonexistent",
            "aws_region": "eu-west-2",
        },
    )

    assert response.status_code == 200
    assert "ref not found" in response.text


def test_check_domain_reports_true_when_a_hosted_zone_is_found(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core, "find_hosted_zone_id", lambda domain, region, session=None: "Z_FOUND"
    )

    response = client.get("/deployments/check-domain", params={"domain": "glow.oxrse.uk"})

    assert response.status_code == 200
    assert response.json() == {"auto": True}


def test_check_domain_reports_false_when_no_hosted_zone_is_found(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core, "find_hosted_zone_id", lambda domain, region, session=None: None
    )

    response = client.get("/deployments/check-domain", params={"domain": "example.com"})

    assert response.status_code == 200
    assert response.json() == {"auto": False}


# ---------------------------------------------------------------------------
# Deployment detail, update, logs
# ---------------------------------------------------------------------------


def _stub_deployment(monkeypatch):
    monkeypatch.setattr(
        core,
        "list_deployments",
        lambda session, region: [
            {
                "instance_id": "i-123",
                "state": "running",
                "domain": "example.com",
                "git_ref": "main",
                "git_commit": "a" * 40,
                "launch_time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        core,
        "list_snapshots",
        lambda region, session, domain=None: [],
    )


def test_deployment_detail_renders_for_known_domain(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)

    response = client.get("/deployments/example.com")

    assert response.status_code == 200
    assert "example.com" in response.text


def test_deployment_detail_404s_for_unknown_domain(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)

    response = client.get("/deployments/does-not-exist.com")

    assert response.status_code == 404


def test_deployment_detail_lists_snapshots_for_the_domain(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "list_snapshots",
        lambda region, session, domain=None: [
            {
                "snapshot_id": "snap-1",
                "domain": domain,
                "reason": "pre-update",
                "started_at": "2026-01-01T00:00:00Z",
                "size_gb": 100,
                "state": "completed",
            }
        ],
    )

    response = client.get("/deployments/example.com")

    assert response.status_code == 200
    assert "snap-1" in response.text
    assert "pre-update" in response.text


def test_deployment_detail_delete_snapshot_route(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(core, "list_snapshots", lambda region, session, domain=None: [])
    delete_calls = []
    monkeypatch.setattr(
        core,
        "delete_snapshot",
        lambda snapshot_id, region, session: delete_calls.append(snapshot_id),
    )

    response = client.post(
        "/deployments/example.com/snapshots/snap-1/delete", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/deployments/example.com"
    assert delete_calls == ["snap-1"]


def test_update_plan_then_apply_updates(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        github_api, "resolve_git_commit_via_github", lambda repo_url, ref: "d" * 40
    )
    update_calls = []
    monkeypatch.setattr(
        core, "update", lambda config: update_calls.append(config) or core.write_line("done")
    )

    plan_response = client.post(
        "/deployments/example.com/update/plan",
        data={"git_repo_url": "https://github.com/OxWRC/glow.git", "git_ref": "v2"},
        follow_redirects=False,
    )
    assert plan_response.status_code == 303
    plan_job_id = plan_response.headers["location"].removeprefix("/jobs/")
    plan_status = _wait_for_job(client, plan_job_id)
    assert plan_status["status"] == "succeeded"
    assert update_calls[0].dry_run is True

    apply_response = client.post(
        "/deployments/example.com/update/apply",
        data={
            "domain_name": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "v2",
            "git_commit": "d" * 40,
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
        },
        follow_redirects=False,
    )
    assert apply_response.status_code == 303
    apply_job_id = apply_response.headers["location"].removeprefix("/jobs/")
    apply_status = _wait_for_job(client, apply_job_id)
    assert apply_status["status"] == "succeeded"
    assert len(update_calls) == 2
    assert update_calls[1].dry_run is False


def test_new_deployment_plan_falls_back_to_highest_available_version(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(deps, "get_cached_release_tags", lambda request: ["v1.0.0", "v1.4.0"])
    resolved_refs = []
    monkeypatch.setattr(
        github_api,
        "resolve_git_commit_via_github",
        lambda repo_url, ref: resolved_refs.append(ref) or "c" * 40,
    )
    monkeypatch.setattr(core, "provision", lambda config: core.write_line("done"))

    response = client.post(
        "/deployments/new/plan",
        data={
            "domain": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "",
            "git_ref_override": "",
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
            "runner_instance_type": "t3.medium",
            "runner_root_volume_size_gb": "100",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolved_refs == ["v1.4.0"]


def test_new_deployment_plan_override_takes_precedence_over_version_select(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(deps, "get_cached_release_tags", lambda request: ["v1.4.0"])
    resolved_refs = []
    monkeypatch.setattr(
        github_api,
        "resolve_git_commit_via_github",
        lambda repo_url, ref: resolved_refs.append(ref) or "c" * 40,
    )
    monkeypatch.setattr(core, "provision", lambda config: core.write_line("done"))

    response = client.post(
        "/deployments/new/plan",
        data={
            "domain": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "v1.4.0",
            "git_ref_override": "my-feature-branch",
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
            "runner_instance_type": "t3.medium",
            "runner_root_volume_size_gb": "100",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert resolved_refs == ["my-feature-branch"]


def test_update_plan_shows_currently_running_and_deploying_in_job_progress(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(core, "get_deployed_version", lambda domain_name, timeout=5.0: "v1.2.0")
    monkeypatch.setattr(
        github_api, "resolve_git_commit_via_github", lambda repo_url, ref: "d" * 40
    )
    monkeypatch.setattr(core, "update", lambda config: core.write_line("done"))

    plan_response = client.post(
        "/deployments/example.com/update/plan",
        data={"git_repo_url": "https://github.com/OxWRC/glow.git", "git_ref": "v1.4.0"},
        follow_redirects=False,
    )
    plan_job_id = plan_response.headers["location"].removeprefix("/jobs/")
    _wait_for_job(client, plan_job_id)

    job_page = client.get(f"/jobs/{plan_job_id}")
    assert "v1.2.0" in job_page.text
    assert "v1.4.0" in job_page.text


def test_deployment_detail_shows_custom_ref_note_for_main_tracked_deployment(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(core, "get_deployed_version", lambda domain_name, timeout=5.0: "dev")

    response = client.get("/deployments/example.com")

    assert response.status_code == 200
    assert "custom ref" in response.text
    assert "Update available" not in response.text
    assert "Major upgrade available" not in response.text


def test_deployment_detail_shows_unreachable_note_when_api_does_not_respond(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    # autouse fixture already defaults get_deployed_version to None (unreachable)

    response = client.get("/deployments/example.com")

    assert response.status_code == 200
    assert "Couldn't reach the deployed API" in response.text


def test_home_shows_no_update_badge_for_main_tracked_deployment(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core,
        "list_deployments",
        lambda session, region: [
            {
                "instance_id": "i-123",
                "state": "running",
                "domain": "example.com",
                "git_ref": "main",
                "git_commit": "a" * 40,
                "launch_time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(core, "get_cpu_utilization", lambda ids, region, session: {})
    monkeypatch.setattr(core, "get_deployed_version", lambda domain_name, timeout=5.0: "dev")

    response = client.get("/deployments")

    assert response.status_code == 200
    assert "update available" not in response.text
    assert "upgrade available" not in response.text


def test_home_shows_update_badge_when_a_newer_version_is_available(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core,
        "list_deployments",
        lambda session, region: [
            {
                "instance_id": "i-123",
                "state": "running",
                "domain": "example.com",
                "git_ref": "v1.2.0",
                "git_commit": "a" * 40,
                "launch_time": "2026-01-01T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(core, "get_cpu_utilization", lambda ids, region, session: {})
    monkeypatch.setattr(core, "get_deployed_version", lambda domain_name, timeout=5.0: "v1.2.0")
    monkeypatch.setattr(deps, "get_cached_release_tags", lambda request: ["v1.4.0"])

    response = client.get("/deployments")

    assert response.status_code == 200
    assert "update available" in response.text


def test_logs_route_surfaces_runner_status(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_runner_status",
        lambda instance_id, region, session: {
            "health": "ok",
            "git_ref": "main",
            "git_commit": "a" * 40,
        },
    )
    monkeypatch.setattr(
        core,
        "get_container_logs",
        lambda instance_id, domain_name, region, session: {"glow-web-1": ["line one"]},
    )

    response = client.get("/deployments/example.com/logs")
    assert response.status_code == 200
    assert "Checking server status" in response.text

    monkeypatch.setattr(
        core,
        "get_admin_credentials",
        lambda instance_id, region, session: {"email": "glow-admin@example.com", "password": "s3cret"},
    )

    status_response = client.get("/deployments/example.com/logs/status")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"]["health"] == "ok"
    assert body["admin_credentials"] == {"email": "glow-admin@example.com", "password": "s3cret"}
    assert body["containers"] == {"glow-web-1": ["line one"]}


def test_logs_route_surfaces_deploy_errors(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_runner_status",
        lambda instance_id, region, session: (_ for _ in ()).throw(
            DeployError("SSM offline")
        ),
    )
    monkeypatch.setattr(
        core,
        "get_container_logs",
        lambda instance_id, domain_name, region, session: (_ for _ in ()).throw(
            DeployError("CloudWatch offline")
        ),
    )

    status_response = client.get("/deployments/example.com/logs/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert "Couldn't reach the server" in body["error"]
    assert body["admin_credentials"] is None
    assert body["admin_credentials_error"] is None
    assert "Couldn't fetch container logs" in body["containers_error"]


def test_logs_route_surfaces_admin_credentials_error(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_runner_status",
        lambda instance_id, region, session: {"health": "ok", "git_ref": "main", "git_commit": "a" * 40},
    )
    monkeypatch.setattr(
        core,
        "get_container_logs",
        lambda instance_id, domain_name, region, session: {},
    )
    monkeypatch.setattr(
        core,
        "get_admin_credentials",
        lambda instance_id, region, session: (_ for _ in ()).throw(
            DeployError("file not found")
        ),
    )

    status_response = client.get("/deployments/example.com/logs/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["admin_credentials"] is None
    assert "Couldn't fetch dashboard admin credentials" in body["admin_credentials_error"]


def test_container_log_tail_route_returns_lines(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_container_log_tail",
        lambda instance_id, domain_name, container_name, region, session: ["new line"],
    )

    response = client.get("/deployments/example.com/logs/containers/glow-web-1/tail")

    assert response.status_code == 200
    assert response.json() == {"lines": ["new line"], "error": None}


def test_container_log_tail_route_surfaces_deploy_errors(client, monkeypatch):
    _sign_in(client)
    _stub_deployment(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_container_log_tail",
        lambda instance_id, domain_name, container_name, region, session: (_ for _ in ()).throw(
            DeployError("CloudWatch offline")
        ),
    )

    response = client.get("/deployments/example.com/logs/containers/glow-web-1/tail")

    assert response.status_code == 200
    body = response.json()
    assert body["lines"] is None
    assert "Couldn't fetch logs for glow-web-1" in body["error"]
