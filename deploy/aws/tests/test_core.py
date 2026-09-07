import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import glow_deploy.core as core

AWS_DEPLOY_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DEPLOY_DIR.parents[1]


# ---------------------------------------------------------------------------
# AMI helpers
# ---------------------------------------------------------------------------


def test_extract_ami_id_from_packer_output_reads_machine_readable_artifact_id():
    output = "\n".join(
        [
            "1720781200,,ui,say,Building AMI",
            "1720781201,amazon-ebs.runner,artifact,0,id,eu-west-2:ami-0123456789abcdef0",
        ]
    )

    assert core.extract_ami_id_from_packer_output(output) == "ami-0123456789abcdef0"


def test_extract_ami_id_from_packer_output_ignores_trailing_control_characters():
    output = (
        "1720781201,amazon-ebs.runner,artifact,0,id,"
        "eu-west-2:ami-0123456789abcdef0[0m"
    )

    assert core.extract_ami_id_from_packer_output(output) == "ami-0123456789abcdef0"


def test_extract_ami_id_from_packer_output_rejects_missing_artifact_id():
    with pytest.raises(core.DeployError, match="could not extract AMI ID"):
        core.extract_ami_id_from_packer_output("1720781200,,ui,say,Building AMI")


def test_validate_ami_id_rejects_invalid_characters():
    with pytest.raises(core.DeployError, match="invalid AMI ID"):
        core.validate_ami_id("ami-01234567\x07")


# ---------------------------------------------------------------------------
# Progress sink
# ---------------------------------------------------------------------------


def test_write_line_uses_default_stderr_sink(capsys):
    core.write_line("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.err


def test_set_progress_sink_redirects_write_line_and_write_inline():
    messages: list[tuple[str, bool, bool]] = []
    token = core.set_progress_sink(
        lambda message, inline, detail: messages.append((message, inline, detail))
    )
    try:
        core.write_line("progress")
        core.write_line("tailed", detail=True)
        core.write_inline("spinner")
    finally:
        core.reset_progress_sink(token)

    assert messages == [
        ("progress", False, False),
        ("tailed", False, True),
        ("spinner", True, False),
    ]


# ---------------------------------------------------------------------------
# run_command / session credential plumbing
# ---------------------------------------------------------------------------


class _FakeFrozenCredentials:
    def __init__(self, access_key, secret_key, token=None):
        self.access_key = access_key
        self.secret_key = secret_key
        self.token = token


class _FakeCredentials:
    def __init__(self, frozen):
        self._frozen = frozen

    def get_frozen_credentials(self):
        return self._frozen


class _FakeSession:
    def __init__(self, frozen):
        self._frozen = frozen
        self.client_calls: list[tuple[str, str]] = []

    def get_credentials(self):
        return _FakeCredentials(self._frozen)

    def client(self, service_name, region_name=None):
        self.client_calls.append((service_name, region_name))
        return SimpleNamespace()


def test_run_command_forwards_env_to_subprocess(monkeypatch):
    captured = {}

    def fake_run(args, capture_output, text, cwd, env):
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    core.run_command(["echo", "hi"], env={"FOO": "bar"})

    assert captured["env"] == {"FOO": "bar"}


def test_subprocess_env_returns_none_without_a_session():
    assert core._subprocess_env(None) is None


def test_subprocess_env_injects_frozen_credentials_over_ambient_environment(
    monkeypatch,
):
    monkeypatch.setenv("PATH", "/usr/bin")
    session = _FakeSession(_FakeFrozenCredentials("AKIAEXAMPLE", "secret", "token"))

    env = core._subprocess_env(session)

    assert env["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert env["AWS_SESSION_TOKEN"] == "token"
    assert env["PATH"] == "/usr/bin"


def test_subprocess_env_omits_session_token_for_long_term_credentials():
    session = _FakeSession(_FakeFrozenCredentials("AKIAEXAMPLE", "secret", token=None))

    env = core._subprocess_env(session)

    assert "AWS_SESSION_TOKEN" not in env


def test_client_uses_session_when_provided():
    session = _FakeSession(_FakeFrozenCredentials("AKIAEXAMPLE", "secret"))

    core._client(session, "ec2", "eu-west-2")

    assert session.client_calls == [("ec2", "eu-west-2")]


# ---------------------------------------------------------------------------
# terraform outputs
# ---------------------------------------------------------------------------


def test_read_terraform_outputs_parses_terraform_json(monkeypatch):
    monkeypatch.setattr(core.binaries, "terraform_binary", lambda: "terraform")
    monkeypatch.setattr(
        core,
        "run_command",
        lambda args, check=True, cwd=None, env=None: SimpleNamespace(
            stdout=(
                '{"runner_instance_id": {"value": "i-1234567890"}, '
                '"alb_dns_name": {"value": "alb.example.com"}}'
            )
        ),
    )

    assert core.read_terraform_outputs() == {
        "runner_instance_id": "i-1234567890",
        "alb_dns_name": "alb.example.com",
    }


# ---------------------------------------------------------------------------
# list_deployments
# ---------------------------------------------------------------------------


class _FakeEc2Client:
    def __init__(self, response=None):
        self._response = response
        self.describe_instances_calls: list[dict] = []
        self.create_tags_calls: list[dict] = []

    def describe_instances(self, **kwargs):
        self.describe_instances_calls.append(kwargs)
        return self._response

    def create_tags(self, **kwargs):
        self.create_tags_calls.append(kwargs)


def test_list_deployments_maps_tags_from_terraform_managed_instances(monkeypatch):
    response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-1234567890",
                        "State": {"Name": "running"},
                        "LaunchTime": "2026-01-01T00:00:00Z",
                        "Tags": [
                            {"Key": "Component", "Value": "glow-runner"},
                            {"Key": "Domain", "Value": "eu.glow-project.org"},
                            {"Key": "GitRef", "Value": "main"},
                            {"Key": "GitCommit", "Value": "deadbeef"},
                        ],
                    }
                ]
            }
        ]
    }
    fake_ec2 = _FakeEc2Client(response)
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    deployments = core.list_deployments(session=None, region="eu-west-2")

    assert deployments == [
        {
            "instance_id": "i-1234567890",
            "state": "running",
            "domain": "eu.glow-project.org",
            "git_ref": "main",
            "git_commit": "deadbeef",
            "launch_time": "2026-01-01T00:00:00Z",
        }
    ]
    assert fake_ec2.describe_instances_calls[0]["Filters"][0] == {
        "Name": "tag:Component",
        "Values": ["glow-runner"],
    }


class _FakeRoute53Client:
    def __init__(self, zones):
        self._zones = zones

    def get_paginator(self, operation_name):
        assert operation_name == "list_hosted_zones"
        return self

    def paginate(self):
        yield {"HostedZones": self._zones}


def _zone(name, zone_id, private=False):
    return {
        "Name": name,
        "Id": f"/hostedzone/{zone_id}",
        "Config": {"PrivateZone": private},
    }


def test_find_hosted_zone_id_picks_the_most_specific_matching_zone(monkeypatch):
    fake_route53 = _FakeRoute53Client(
        [_zone("oxrse.uk.", "Z_PARENT"), _zone("glow.oxrse.uk.", "Z_CHILD")]
    )
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_route53)

    assert core.find_hosted_zone_id("glow.oxrse.uk", "eu-west-2") == "Z_CHILD"


def test_find_hosted_zone_id_falls_back_to_a_parent_zone(monkeypatch):
    fake_route53 = _FakeRoute53Client([_zone("oxrse.uk.", "Z_PARENT")])
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_route53)

    assert core.find_hosted_zone_id("glow.oxrse.uk", "eu-west-2") == "Z_PARENT"


def test_find_hosted_zone_id_ignores_private_zones(monkeypatch):
    fake_route53 = _FakeRoute53Client([_zone("oxrse.uk.", "Z_PRIVATE", private=True)])
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_route53)

    assert core.find_hosted_zone_id("glow.oxrse.uk", "eu-west-2") is None


def test_find_hosted_zone_id_returns_none_for_an_unrelated_domain(monkeypatch):
    fake_route53 = _FakeRoute53Client([_zone("example.com.", "Z_OTHER")])
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_route53)

    assert core.find_hosted_zone_id("glow.oxrse.uk", "eu-west-2") is None


def test_terraform_apply_requires_a_hosted_zone_or_a_pasted_certificate_arn(
    monkeypatch,
):
    monkeypatch.setattr(core, "find_hosted_zone_id", lambda domain, region, session=None: None)
    config = _make_config(domain_name="example.com", certificate_arn="")

    with pytest.raises(core.DeployError, match="no public Route 53 hosted zone"):
        core.terraform_apply(config, "ami-12345678")


def test_terraform_apply_prefers_a_pasted_certificate_arn_over_auto_dns(monkeypatch):
    """A pasted ARN is how someone hosting on another registrar's domain opts
    out of auto-DNS — it must win even if this account also has a matching
    hosted zone, and the lookup should be skipped entirely (no AWS call)."""
    lookup_calls = []
    monkeypatch.setattr(
        core,
        "find_hosted_zone_id",
        lambda domain, region, session=None: lookup_calls.append(domain) or "Z_FOUND",
    )
    monkeypatch.setattr(core.binaries, "terraform_binary", lambda: "terraform")

    captured_tfvars = {}

    def fake_run_command(args, **kwargs):
        tfvars_path = next(a for a in args if a.startswith("-var-file=")).split("=", 1)[1]
        captured_tfvars.update(json.loads(Path(tfvars_path).read_text()))
        return SimpleNamespace(stdout="plan output")

    monkeypatch.setattr(core, "run_command", fake_run_command)

    config = _make_config(
        domain_name="example.com", certificate_arn="arn:aws:acm:...", dry_run=True
    )
    core.terraform_apply(config, "ami-12345678")

    assert lookup_calls == []
    assert captured_tfvars["hosted_zone_id"] == ""
    assert captured_tfvars["certificate_arn"] == "arn:aws:acm:..."


# ---------------------------------------------------------------------------
# get_runner_status
# ---------------------------------------------------------------------------


def test_get_runner_status_aggregates_health_and_git_ref(monkeypatch):
    outputs = {
        "check runner health": "[SUCCESS] Runner healthcheck passed\n",
        "read deployed git ref": "main\n",
        "read deployed git commit": "deadbeef\n",
    }

    def fake_capture(instance_id, region, commands, comment, timeout=300, session=None):
        return outputs[comment]

    monkeypatch.setattr(core, "run_ssm_command_capturing_output", fake_capture)

    status = core.get_runner_status("i-1234567890", "eu-west-2")

    assert status == {
        "health": "[SUCCESS] Runner healthcheck passed",
        "git_ref": "main",
        "git_commit": "deadbeef",
    }


# ---------------------------------------------------------------------------
# get_admin_credentials
# ---------------------------------------------------------------------------


def test_get_admin_credentials_parses_env_file(monkeypatch):
    def fake_capture(instance_id, region, commands, comment, timeout=300, session=None):
        return "ODK_ADMIN_EMAIL=glow-admin@example.com\nODK_ADMIN_PASSWORD=s3cret\n"

    monkeypatch.setattr(core, "run_ssm_command_capturing_output", fake_capture)

    creds = core.get_admin_credentials("i-1234567890", "eu-west-2")

    assert creds == {"email": "glow-admin@example.com", "password": "s3cret"}


def test_get_admin_credentials_raises_on_missing_fields(monkeypatch):
    monkeypatch.setattr(
        core, "run_ssm_command_capturing_output", lambda *a, **k: "SOME_OTHER_VAR=x\n"
    )

    with pytest.raises(core.DeployError):
        core.get_admin_credentials("i-1234567890", "eu-west-2")


# ---------------------------------------------------------------------------
# get_deployed_version
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status_code=200, json_data=None, raise_exc=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_get_deployed_version_returns_version_from_live_api(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeHttpResponse(json_data={"version": "v1.2.3"})

    monkeypatch.setattr(core.httpx, "get", fake_get)

    assert core.get_deployed_version("example.com") == "v1.2.3"
    assert captured["url"] == "https://api.example.com/"
    assert captured["timeout"] == 5.0


def test_get_deployed_version_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(
        core.httpx, "get", lambda url, timeout=None: _FakeHttpResponse(status_code=500)
    )

    assert core.get_deployed_version("example.com") is None


def test_get_deployed_version_returns_none_on_missing_version_key(monkeypatch):
    monkeypatch.setattr(
        core.httpx, "get", lambda url, timeout=None: _FakeHttpResponse(json_data={})
    )

    assert core.get_deployed_version("example.com") is None


def test_get_deployed_version_returns_none_on_timeout(monkeypatch):
    def fake_get(url, timeout=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(core.httpx, "get", fake_get)

    assert core.get_deployed_version("example.com") is None


# ---------------------------------------------------------------------------
# get_container_logs
# ---------------------------------------------------------------------------


class _FakeLogsClient:
    def __init__(self, streams, events_by_stream):
        self._streams = streams
        self._events_by_stream = events_by_stream
        self.describe_calls = []
        self.get_events_calls = []

    def get_paginator(self, name):
        assert name == "describe_log_streams"
        return self

    def paginate(self, **kwargs):
        self.describe_calls.append(kwargs)
        yield {"logStreams": self._streams}

    def get_log_events(self, **kwargs):
        self.get_events_calls.append(kwargs)
        return {"events": self._events_by_stream[kwargs["logStreamName"]]}


def test_get_container_logs_groups_by_container_name(monkeypatch):
    streams = [
        {"logStreamName": "i-1234567890-glow-web-1"},
        {"logStreamName": "i-1234567890-glow-worker-1"},
    ]
    events_by_stream = {
        "i-1234567890-glow-web-1": [{"message": "web line"}],
        "i-1234567890-glow-worker-1": [{"message": "worker line"}],
    }
    fake_logs = _FakeLogsClient(streams, events_by_stream)
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_logs)

    result = core.get_container_logs("i-1234567890", "example.com", "eu-west-2")

    assert result == {
        "glow-web-1": ["web line"],
        "glow-worker-1": ["worker line"],
    }
    assert fake_logs.describe_calls[0]["logGroupName"] == "/glow/example.com/containers"
    assert fake_logs.describe_calls[0]["logStreamNamePrefix"] == "i-1234567890-"


def test_get_container_log_tail_reads_single_stream_directly(monkeypatch):
    fake_logs = _FakeLogsClient(
        streams=[],
        events_by_stream={"i-1234567890-glow-web-1": [{"message": "web line"}]},
    )
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_logs)

    result = core.get_container_log_tail("i-1234567890", "example.com", "glow-web-1", "eu-west-2")

    assert result == ["web line"]
    assert fake_logs.describe_calls == []
    assert fake_logs.get_events_calls[0]["logGroupName"] == "/glow/example.com/containers"
    assert fake_logs.get_events_calls[0]["logStreamName"] == "i-1234567890-glow-web-1"


class _SequencedLogsClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get_log_events(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def test_tail_new_cloudwatch_lines_filters_agent_noise_and_advances_token():
    fake_logs = _SequencedLogsClient(
        [
            {
                "events": [
                    {"message": "I! Trying to detect region from ec2"},
                    {"message": "Configuration validation succeeded"},
                    {"message": "[PROGRESS] Activate stack"},
                ],
                "nextForwardToken": "token-1",
            },
            {
                "events": [{"message": "[PROGRESS] Building Dashboard"}],
                "nextForwardToken": "token-2",
            },
        ]
    )

    poll = core._tail_new_cloudwatch_lines(
        fake_logs, "/glow/example.com/bootstrap", "i-1234567890/runner-bootstrap"
    )

    assert poll() == ["[PROGRESS] Activate stack"]
    assert poll() == ["[PROGRESS] Building Dashboard"]
    assert fake_logs.calls[0]["startFromHead"] is True
    assert "startTime" in fake_logs.calls[0]
    assert "nextToken" not in fake_logs.calls[0]
    assert fake_logs.calls[1]["nextToken"] == "token-1"
    assert "startTime" not in fake_logs.calls[1]


# ---------------------------------------------------------------------------
# SSM command helpers
# ---------------------------------------------------------------------------


def test_rerun_runner_userdata_reports_last_bootstrap_log_line(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_ssm_command(
        instance_id, region, commands, comment, timeout=1800, session=None, on_tick=None
    ):
        captured["instance_id"] = instance_id
        captured["region"] = region
        captured["commands"] = commands
        captured["comment"] = comment
        captured["timeout"] = timeout

    monkeypatch.setattr(core, "run_ssm_command", fake_run_ssm_command)
    monkeypatch.setattr(core, "_client", lambda session, service, region: SimpleNamespace())

    core.rerun_runner_userdata("i-1234567890", "eu-west-2", "example.com")

    assert captured["instance_id"] == "i-1234567890"
    assert captured["region"] == "eu-west-2"
    assert captured["comment"] == "rerun runner userdata"
    assert captured["timeout"] == 3600

    command = captured["commands"][0]
    assert "tail -n 1 /var/log/glow-runner-bootstrap.log" in command
    assert "last bootstrap log line:" in command


def test_rerun_runner_userdata_accepts_git_environment_overrides(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_ssm_command(
        instance_id, region, commands, comment, timeout=1800, session=None, on_tick=None
    ):
        captured["commands"] = commands

    monkeypatch.setattr(core, "run_ssm_command", fake_run_ssm_command)
    monkeypatch.setattr(core, "_client", lambda session, service, region: SimpleNamespace())

    core.rerun_runner_userdata(
        "i-1234567890",
        "eu-west-2",
        "example.com",
        {
            "GIT_REPO_URL": "https://example.com/glow.git",
            "GIT_REF": "v1.2.3",
            "GIT_COMMIT": "deadbeef",
        },
    )

    command = captured["commands"][0]
    assert "export GIT_REPO_URL=https://example.com/glow.git" in command
    assert "export GIT_REF=v1.2.3" in command
    assert "export GIT_COMMIT=deadbeef" in command


def test_prepare_runner_repository_clones_and_checks_out_requested_ref(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_ssm_command(instance_id, region, commands, comment, timeout=1800, session=None):
        captured["instance_id"] = instance_id
        captured["region"] = region
        captured["commands"] = commands
        captured["comment"] = comment
        captured["timeout"] = timeout

    monkeypatch.setattr(core, "run_ssm_command", fake_run_ssm_command)

    core.prepare_runner_repository(
        "i-1234567890",
        "eu-west-2",
        "https://example.com/glow.git",
        "deadbeef",
    )

    assert captured["comment"] == "prepare runner repository"
    assert captured["timeout"] == 3600

    command = captured["commands"][0]
    assert 'git clone "${repo_url}" /opt/glow' in command
    assert 'git -C /opt/glow checkout --force "${checkout_ref}"' in command
    assert "repo_url=https://example.com/glow.git" in command
    assert "checkout_ref=deadbeef" in command


def test_wait_for_runner_bootstrap_completion_waits_for_ready_file(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_ssm_command(instance_id, region, commands, comment, timeout=1800, session=None):
        captured["instance_id"] = instance_id
        captured["region"] = region
        captured["commands"] = commands
        captured["comment"] = comment
        captured["timeout"] = timeout

    monkeypatch.setattr(core, "run_ssm_command", fake_run_ssm_command)

    core.wait_for_runner_bootstrap_completion("i-1234567890", "eu-west-2")

    assert captured["instance_id"] == "i-1234567890"
    assert captured["region"] == "eu-west-2"
    assert captured["comment"] == "wait for runner bootstrap completion"
    assert captured["timeout"] == 1800
    assert captured["commands"] == [
        "timeout 300 bash -c 'while [ ! -f /opt/glow-runner/bootstrap.ready ]; do sleep 1; done'"
    ]


# ---------------------------------------------------------------------------
# Shell script/template content (unchanged by this refactor, still verified)
# ---------------------------------------------------------------------------


def test_runner_userdata_prefers_git_environment_over_template_defaults():
    template_path = AWS_DEPLOY_DIR / "templates" / "runner-userdata.sh.tpl"
    template = template_path.read_text()

    assert 'GIT_REPO_URL="$${GIT_REPO_URL:-${git_repo_url}}"' in template
    assert 'GIT_REF="$${GIT_REF:-${git_ref}}"' in template
    assert 'GIT_COMMIT="$${GIT_COMMIT:-${git_checkout_ref}}"' in template


def test_runner_userdata_marks_bootstrap_ready_before_waiting_for_repository_checkout():
    template_path = AWS_DEPLOY_DIR / "templates" / "runner-userdata.sh.tpl"
    template = template_path.read_text()

    wait_line = (
        '  echo "[PROGRESS] Repository checkout not present yet; waiting for deploy.py '
        'to prepare it"'
    )
    assert "touch /opt/glow-runner/bootstrap.ready" in template
    assert wait_line in template
    assert template.index("touch /opt/glow-runner/bootstrap.ready") < template.index(
        wait_line
    )


def test_runner_userdata_persists_git_ref_and_commit_in_environment_files():
    template_path = AWS_DEPLOY_DIR / "templates" / "runner-userdata.sh.tpl"
    template = template_path.read_text()

    assert "GIT_REF=$${GIT_REF}" in template
    assert "GIT_COMMIT=$${GIT_COMMIT}" in template
    assert "/etc/environment" in template
    assert "GIT_REF=\"$${GIT_REF}\"" in template
    assert "GIT_COMMIT=\"$${GIT_COMMIT}\"" in template


def test_runner_userdata_uses_var_lib_glow_for_persistent_state_check():
    template_path = AWS_DEPLOY_DIR / "templates" / "runner-userdata.sh.tpl"
    template = template_path.read_text()

    assert "/var/lib/glow/.mnttest" in template
    assert "/data/.mnttest" not in template


def test_runner_userdata_does_not_clone_or_checkout_repository():
    template_path = AWS_DEPLOY_DIR / "templates" / "runner-userdata.sh.tpl"
    template = template_path.read_text()

    assert "git clone" not in template
    assert "git -C /opt/glow checkout --force" not in template


def test_activate_stack_configures_odk_without_querying_users_id():
    script_path = AWS_DEPLOY_DIR / "runtime" / "activate-stack.sh"
    script = script_path.read_text()

    assert "SELECT id FROM users" not in script
    assert "user-create 2>&1 || true" in script
    assert "user-set-password" in script


def test_activate_stack_writes_requested_git_ref_and_commit_to_metadata():
    script_path = AWS_DEPLOY_DIR / "runtime" / "activate-stack.sh"
    script = script_path.read_text()

    assert '"git_ref": "${GIT_REF:-}",' in script
    assert '"git_commit": "${checkout_ref}"' in script


def test_activate_stack_computes_app_version_from_git_describe():
    script_path = AWS_DEPLOY_DIR / "runtime" / "activate-stack.sh"
    script = script_path.read_text()

    assert "compute_app_version" in script
    assert (
        "git -C \"${WORK_DIR}\" describe --tags --match 'v[0-9]*.[0-9]*.[0-9]*'"
        in script
    )
    assert "export APP_VERSION" in script
    assert "  compute_app_version\n  start_stack" in script


def test_activate_stack_uses_odk_domain_for_helper_host_header_and_ping():
    script_path = AWS_DEPLOY_DIR / "runtime" / "activate-stack.sh"
    script = script_path.read_text()

    assert 'export ODK_DOMAIN="odk.${DOMAIN_NAME}"' in script
    assert 'info "> odk_ping"' in script
    assert 'if odk_ping >/dev/null 2>&1; then' in script
    assert "curl -fsS -H \"Host: odk.$DOMAIN_NAME\" http://127.0.0.1:8080/" not in script
    assert 'curl -fsS http://127.0.0.1:8080/ >/dev/null' not in script


def test_odk_api_helper_supports_optional_host_header_and_ping():
    script_path = REPO_ROOT / "scripts" / "odk" / "odk-api-helper.sh"
    script = script_path.read_text()

    assert 'ODK_HOST_HEADER="${ODK_DOMAIN:-}"' in script
    assert 'odk_curl() {' in script
    assert 'curl -H "Host: ${ODK_HOST_HEADER}" "$@"' in script
    assert 'odk_ping() {' in script
    assert 'local root_url="${ODK_API_BASE%/v1}/"' in script
    assert 'odk_curl -fsS "${root_url}"' in script


def test_get_git_ref_script_reads_runner_environment_file():
    script_path = AWS_DEPLOY_DIR / "runtime" / "get-git-ref.sh"
    script = script_path.read_text()

    assert 'ENV_FILE="/etc/glow-runner.env"' in script
    assert 'case "${1:-}" in' in script
    assert '--commit)' in script
    assert 'printf "%s\\n" "${GIT_REF:-}"' in script
    assert 'printf "%s\\n" "${GIT_COMMIT:-}"' in script


# ---------------------------------------------------------------------------
# provision() / update() orchestration
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> core.Config:
    defaults = dict(
        domain_name="example.com",
        git_repo_url="https://example.com/glow.git",
        git_ref="main",
        git_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        aws_region="eu-west-2",
        app_name="glow-core",
        runner_instance_type="t3.medium",
        runner_root_volume_size_gb=100,
        dry_run=False,
        force_rebuild_ami=False,
    )
    defaults.update(overrides)
    return core.Config(**defaults)


def test_update_prepares_repository_before_rerunning_userdata(monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core, "terraform_init", lambda bucket, region, session=None: None
    )
    monkeypatch.setattr(core.binaries, "terraform_binary", lambda: "terraform")
    monkeypatch.setattr(
        core,
        "run_command",
        lambda args, check=True, cwd=None, env=None: SimpleNamespace(
            stdout='{"runner_instance_id": {"value": "i-1234567890"}}'
        ),
    )
    monkeypatch.setattr(
        core,
        "wait_for_ssm_online",
        lambda instance_id, region, session=None: calls.append(("wait", instance_id)),
    )
    monkeypatch.setattr(
        core,
        "wait_for_runner_bootstrap_completion",
        lambda instance_id, region, session=None: calls.append(
            ("bootstrap", instance_id)
        ),
    )
    monkeypatch.setattr(
        core,
        "prepare_runner_repository",
        lambda instance_id, region, repo_url, checkout_ref, session=None: calls.append(
            ("prepare", (instance_id, region, repo_url, checkout_ref))
        ),
    )
    monkeypatch.setattr(
        core,
        "rerun_runner_userdata",
        lambda instance_id, region, domain_name, env=None, session=None: calls.append(
            ("rerun", env)
        ),
    )
    monkeypatch.setattr(
        core,
        "verify_runner_health",
        lambda instance_id, region, session=None: calls.append(("verify", instance_id)),
    )
    fake_ec2 = _FakeEc2Client()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    core.update(_make_config())

    assert calls == [
        ("wait", "i-1234567890"),
        ("bootstrap", "i-1234567890"),
        (
            "prepare",
            (
                "i-1234567890",
                "eu-west-2",
                "https://example.com/glow.git",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ),
        ),
        (
            "rerun",
            {
                "GIT_REPO_URL": "https://example.com/glow.git",
                "GIT_REF": "main",
                "GIT_COMMIT": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            },
        ),
        ("verify", "i-1234567890"),
    ]
    assert fake_ec2.create_tags_calls == [
        {
            "Resources": ["i-1234567890"],
            "Tags": [
                {"Key": "GitRef", "Value": "main"},
                {"Key": "GitCommit", "Value": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"},
                {"Key": "GitTag", "Value": "main"},
                {"Key": "GlowGUIVersion", "Value": "dev"},
            ],
        }
    ]


def test_provision_prepares_repository_before_rerunning_userdata(monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core,
        "find_ami_in_account",
        lambda region, commit, session=None: "ami-12345678",
    )
    monkeypatch.setattr(
        core, "terraform_init", lambda bucket, region, session=None: None
    )
    monkeypatch.setattr(
        core,
        "terraform_apply",
        lambda config, ami_id: {
            "runner_instance_id": "i-1234567890",
            "alb_dns_name": "alb.example.com",
        },
    )
    monkeypatch.setattr(
        core,
        "wait_for_ssm_online",
        lambda instance_id, region, session=None: calls.append(("wait", instance_id)),
    )
    monkeypatch.setattr(
        core,
        "wait_for_runner_bootstrap_completion",
        lambda instance_id, region, session=None: calls.append(
            ("bootstrap", instance_id)
        ),
    )
    monkeypatch.setattr(
        core,
        "prepare_runner_repository",
        lambda instance_id, region, repo_url, checkout_ref, session=None: calls.append(
            ("prepare", (instance_id, region, repo_url, checkout_ref))
        ),
    )
    monkeypatch.setattr(
        core,
        "rerun_runner_userdata",
        lambda instance_id, region, domain_name, env=None, session=None: calls.append(
            ("rerun", env)
        ),
    )
    monkeypatch.setattr(
        core,
        "verify_runner_health",
        lambda instance_id, region, session=None: calls.append(("verify", instance_id)),
    )

    core.provision(_make_config())

    assert calls == [
        ("wait", "i-1234567890"),
        ("bootstrap", "i-1234567890"),
        (
            "prepare",
            (
                "i-1234567890",
                "eu-west-2",
                "https://example.com/glow.git",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ),
        ),
        (
            "rerun",
            {
                "GIT_REPO_URL": "https://example.com/glow.git",
                "GIT_REF": "main",
                "GIT_COMMIT": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            },
        ),
        ("verify", "i-1234567890"),
    ]


def test_provision_forwards_session_to_every_aws_touching_step(monkeypatch):
    """The whole point of Config.session: every collaborator must see it.

    provision() passes session as the *last positional argument* to each of
    these (see core.py), not as a keyword — so the fakes below deliberately
    grab ``args[-1]`` rather than a ``session=`` keyword, to catch a
    regression where a future edit swaps to keyword-passing without updating
    every call site.
    """
    sessions_seen: list[object] = []
    session = _FakeSession(_FakeFrozenCredentials("AKIAEXAMPLE", "secret"))

    def record_session_and_return(value=None):
        def fake(*args):
            sessions_seen.append(args[-1])
            return value

        return fake

    monkeypatch.setattr(core, "ensure_state_bucket", record_session_and_return("bucket"))
    monkeypatch.setattr(
        core, "find_ami_in_account", record_session_and_return("ami-12345678")
    )
    monkeypatch.setattr(core, "terraform_init", record_session_and_return())
    monkeypatch.setattr(core, "wait_for_ssm_online", record_session_and_return())
    monkeypatch.setattr(
        core, "wait_for_runner_bootstrap_completion", record_session_and_return()
    )
    monkeypatch.setattr(
        core, "prepare_runner_repository", record_session_and_return()
    )
    monkeypatch.setattr(core, "rerun_runner_userdata", record_session_and_return())
    monkeypatch.setattr(core, "verify_runner_health", record_session_and_return())

    def fake_terraform_apply(config, ami_id):
        sessions_seen.append(config.session)
        return {"runner_instance_id": "i-1", "alb_dns_name": "alb"}

    monkeypatch.setattr(core, "terraform_apply", fake_terraform_apply)

    core.provision(_make_config(session=session))

    assert sessions_seen
    assert all(seen is session for seen in sessions_seen)
