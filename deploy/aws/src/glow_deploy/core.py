#!/usr/bin/env python3
"""
Core Glow AWS deployment logic.

This module handles:
- AMI building using local Packer
- One-pass Terraform apply for infrastructure
- SSM-based in-place updates for subsequent releases
- Listing existing deployments (for the GUI's home view)

It is used both by the CLI entry point (``main`` below, installed as the
``glow-deploy`` console script) and by the packaged GUI, which drives the same
functions with a ``boto3.Session`` obtained via AWS SSO instead of relying on
ambient credentials.
"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import httpx

from glow_deploy import binaries, github_api
from glow_deploy.errors import DeployError

if TYPE_CHECKING:
    import boto3

# core.py lives at deploy/aws/src/glow_deploy/core.py, so two parents up is
# deploy/aws.
AWS_DEPLOY_DIR = Path(__file__).resolve().parents[2]
TERRAFORM_DIR = AWS_DEPLOY_DIR / "terraform"
PACKER_DIR = AWS_DEPLOY_DIR / "runner"
AMI_ID_PATTERN = re.compile(r"ami-[0-9a-fA-F]{8,17}")

DEFAULT_GIT_REPO_URL = "https://github.com/OxWRC/glow.git"
CORE_TAG_PREFIX = "v"

# (message, inline, detail) -> None. `inline` means "overwrite the current
# line" (spinner-style); `detail` marks noisy sub-output (e.g. a tailed
# remote log) that a GUI sink may want to fold into a nested block instead of
# interleaving with step lines. Matches write_line/write_inline below.
ProgressSink = Callable[[str, bool, bool], None]


@dataclass
class Config:
    domain_name: str
    git_repo_url: str
    git_ref: str
    git_commit: str
    aws_region: str
    app_name: str
    runner_instance_type: str
    runner_root_volume_size_gb: int
    dry_run: bool
    force_rebuild_ami: bool
    certificate_arn: str = ""
    session: boto3.Session | None = None


def _stderr_progress_sink(message: str, inline: bool, detail: bool) -> None:
    terminator = "" if inline else "\n"
    sys.stderr.write(f"\r\033[K{message}{terminator}")
    sys.stderr.flush()


_progress_sink: contextvars.ContextVar[ProgressSink] = contextvars.ContextVar(
    "progress_sink", default=_stderr_progress_sink
)


def set_progress_sink(sink: ProgressSink) -> contextvars.Token:
    """Install a progress sink for the current context (e.g. a background job).

    Returns a token that can be passed to ``reset_progress_sink`` to restore
    the previous sink, mirroring ``contextvars.ContextVar.reset``.
    """
    return _progress_sink.set(sink)


def reset_progress_sink(token: contextvars.Token) -> None:
    _progress_sink.reset(token)


def write_line(message: str, *, detail: bool = False) -> None:
    """Emit a progress line through the active progress sink.

    ``detail=True`` marks noisy sub-output (e.g. a tailed remote log) so a
    GUI sink can fold it into a nested block instead of interleaving it with
    step lines.
    """
    _progress_sink.get()(message, False, detail)


def write_inline(message: str) -> None:
    """Emit an in-place progress update (spinner) through the active sink."""
    _progress_sink.get()(message, True, False)


def run_command(
    args: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env)
    if check and result.returncode != 0:
        raise DeployError(f"command failed: {' '.join(args)}\n{result.stderr}")
    return result


def _client(session: boto3.Session | None, service_name: str, region: str):
    import boto3

    if session is not None:
        return session.client(service_name, region_name=region)
    return boto3.client(service_name, region_name=region)


def _subprocess_env(session: boto3.Session | None) -> dict[str, str] | None:
    """Build a subprocess environment carrying the session's AWS credentials.

    Terraform and Packer authenticate independently of any ``boto3.Session`` —
    they read ``AWS_*`` environment variables (or ambient CLI config/instance
    metadata). Without this, a GUI-issued session's credentials would never
    reach the terraform/packer subprocesses; they'd silently fall back to
    whatever (if anything) is ambient on the machine.
    """
    if session is None:
        return None

    frozen = session.get_credentials().get_frozen_credentials()
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = frozen.access_key
    env["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key
    if frozen.token:
        env["AWS_SESSION_TOKEN"] = frozen.token
    else:
        env.pop("AWS_SESSION_TOKEN", None)
    return env


def validate_ami_id(ami_id: str) -> str:
    """Ensure an AMI ID is well-formed before passing it to AWS APIs."""
    if not re.fullmatch(AMI_ID_PATTERN, ami_id):
        raise DeployError(f"invalid AMI ID: {ami_id!r}")
    return ami_id


def extract_ami_id_from_packer_output(output: str) -> str:
    """Extract the built AMI ID from Packer machine-readable output."""
    for line in output.splitlines():
        if ",artifact," not in line or ",id," not in line:
            continue

        match = AMI_ID_PATTERN.search(line)
        if match:
            return validate_ami_id(match.group(0))

    raise DeployError("could not extract AMI ID from packer output")


def find_ami_in_account(
    region: str, git_commit: str, session: boto3.Session | None = None
) -> str | None:
    """Find an existing AMI in the AWS account for the given commit."""
    ec2 = _client(session, "ec2", region)
    response = ec2.describe_images(
        Owners=["self"],
        Filters=[
            {"Name": "tag:Component", "Values": ["glow-runner"]},
            {"Name": "tag:GitCommit", "Values": [git_commit]},
        ],
    )

    images = sorted(
        response.get("Images", []),
        key=lambda i: i.get("CreationDate", ""),
        reverse=True,
    )
    if images:
        return images[0]["ImageId"]
    return None


def build_ami_with_packer(
    region: str, git_commit: str, session: boto3.Session | None = None
) -> str:
    """Build the runner AMI using Packer."""
    write_line("[deploy] Building runner AMI with Packer")

    packer_vars = [
        "-var",
        f"aws_region={region}",
        "-var",
        f"git_commit={git_commit}",
    ]

    env = _subprocess_env(session)
    packer = binaries.packer_binary()

    run_command([packer, "init", "packer.pkr.hcl"], cwd=PACKER_DIR, env=env)

    result = run_command(
        [packer, "build", "-machine-readable"] + packer_vars + ["packer.pkr.hcl"],
        cwd=PACKER_DIR,
        env=env,
    )

    return extract_ami_id_from_packer_output(result.stdout)


def ensure_state_bucket(
    region: str, domain_name: str, session: boto3.Session | None = None
) -> str:
    """Ensure the Terraform state bucket exists."""
    sts = _client(session, "sts", region)
    account_id = sts.get_caller_identity()["Account"]

    bucket_name = f"{domain_name.replace('.', '-')}-glow-deploy-state-{account_id}"[
        :63
    ].rstrip("-")

    s3 = _client(session, "s3", region)

    try:
        s3.head_bucket(Bucket=bucket_name)
        write_line(f"[deploy] State bucket exists: {bucket_name}")
        return bucket_name
    except Exception:
        pass

    write_line(f"[deploy] Creating state bucket: {bucket_name}")

    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )

    s3.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    return bucket_name


def find_hosted_zone_id(
    domain_name: str, region: str, session: boto3.Session | None = None
) -> str | None:
    """Find the public Route 53 hosted zone that owns ``domain_name``.

    Walks up from the full domain to find the most specific public zone in
    the account (so a zone for the parent domain is used when deploying a
    subdomain of it), rather than requiring the exact domain to have its own
    zone. Returns ``None`` when no such zone exists in the account (e.g. the
    domain is hosted elsewhere), in which case a pasted-in certificate ARN
    is required instead.
    """
    route53 = _client(session, "route53", region)
    best_id: str | None = None
    best_name_length = -1

    paginator = route53.get_paginator("list_hosted_zones")
    for page in paginator.paginate():
        for zone in page["HostedZones"]:
            if zone.get("Config", {}).get("PrivateZone"):
                continue
            zone_name = zone["Name"].rstrip(".")
            if domain_name == zone_name or domain_name.endswith(f".{zone_name}"):
                if len(zone_name) > best_name_length:
                    best_name_length = len(zone_name)
                    best_id = zone["Id"].rsplit("/", 1)[-1]

    return best_id


def find_conflicting_dns_records(
    hosted_zone_id: str,
    domain_name: str,
    region: str,
    session: boto3.Session | None = None,
) -> list[dict]:
    """Find existing records at the app hostnames that aren't A records.

    We own this hosted zone, so a leftover CNAME (or other record type) at
    glow.example.com / api.example.com / odk.example.com from a previous
    deploy or manual setup shouldn't permanently block us from pointing it at
    the ALB — but Route 53 UPSERT can't change a record's type in place, so
    the old record needs to be deleted first, and the caller decides whether
    that's OK. Returns the raw record sets (as needed to delete them).
    """
    route53 = _client(session, "route53", region)
    conflicts = []

    for prefix in ("", "api.", "odk."):
        name = f"{prefix}{domain_name}"
        response = route53.list_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            StartRecordName=name,
            MaxItems="1",
        )
        record_sets = response["ResourceRecordSets"]
        if not record_sets:
            continue
        record = record_sets[0]
        if record["Name"].rstrip(".") == name and record["Type"] != "A":
            conflicts.append(record)

    return conflicts


def delete_dns_records(
    hosted_zone_id: str,
    records: list[dict],
    region: str,
    session: boto3.Session | None = None,
) -> None:
    """Delete the given Route 53 record sets, as found by ``find_conflicting_dns_records``."""
    route53 = _client(session, "route53", region)

    for record in records:
        write_line(
            f"[deploy] Removing conflicting {record['Type']} record at "
            f"{record['Name'].rstrip('.')}"
        )
        route53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Changes": [{"Action": "DELETE", "ResourceRecordSet": record}]
            },
        )


def terraform_init(
    bucket: str, region: str, session: boto3.Session | None = None
) -> None:
    """Initialize Terraform."""
    run_command(
        [
            binaries.terraform_binary(),
            "init",
            f"-backend-config=bucket={bucket}",
            "-backend-config=key=main.tfstate",
            f"-backend-config=region={region}",
            "-reconfigure",
        ],
        cwd=TERRAFORM_DIR,
        env=_subprocess_env(session),
    )


def read_terraform_outputs(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Parse ``terraform output -json`` into a flat ``{name: value}`` dict."""
    result = run_command(
        [binaries.terraform_binary(), "output", "-json"], cwd=TERRAFORM_DIR, env=env
    )
    raw = json.loads(result.stdout)
    return {name: details["value"] for name, details in raw.items()}


def terraform_apply(config: Config, ami_id: str) -> dict[str, Any]:
    """Apply Terraform configuration."""
    # A pasted certificate ARN always wins: it's how someone hosting on a
    # domain outside this AWS account opts out of auto-DNS, even if this
    # account happens to also have a matching hosted zone.
    hosted_zone_id = ""
    dns_conflicts: list[dict] = []
    if not config.certificate_arn:
        hosted_zone_id = find_hosted_zone_id(
            config.domain_name, config.aws_region, config.session
        )
        if hosted_zone_id:
            write_line(f"[deploy] Using Route 53 hosted zone: {hosted_zone_id}")
            dns_conflicts = find_conflicting_dns_records(
                hosted_zone_id, config.domain_name, config.aws_region, config.session
            )
            if dns_conflicts:
                names = ", ".join(
                    f"{r['Name'].rstrip('.')} ({r['Type']})" for r in dns_conflicts
                )
                write_line(
                    f"[deploy] Existing DNS record(s) at {names} will be deleted "
                    "and replaced with records pointing at this app"
                )
                if not config.dry_run:
                    delete_dns_records(
                        hosted_zone_id,
                        dns_conflicts,
                        config.aws_region,
                        config.session,
                    )
        else:
            raise DeployError(
                f"no public Route 53 hosted zone found for {config.domain_name!r} "
                "in this account, and no certificate ARN was provided. Either "
                "host this domain's DNS here, or paste an existing ACM "
                "certificate ARN for it."
            )

    tfvars = {
        "app_name": config.app_name,
        "aws_region": config.aws_region,
        "hosted_zone_id": hosted_zone_id,
        "certificate_arn": config.certificate_arn,
        "domain_name": config.domain_name,
        "git_ref": config.git_ref,
        "git_repo_url": config.git_repo_url,
        "git_checkout_ref": config.git_commit,
        "runner_ami_id": validate_ami_id(ami_id),
        "runner_instance_type": config.runner_instance_type,
        "runner_root_volume_size_gb": config.runner_root_volume_size_gb,
    }

    fd, tfvars_path = tempfile.mkstemp(suffix=".tfvars.json")
    env = _subprocess_env(config.session)
    terraform = binaries.terraform_binary()
    try:
        Path(tfvars_path).write_text(json.dumps(tfvars, indent=2))

        if config.dry_run:
            result = run_command(
                [terraform, "plan", f"-var-file={tfvars_path}"],
                cwd=TERRAFORM_DIR,
                env=env,
            )
            write_line(result.stdout)
            return {
                "dns_conflicts": [
                    {"name": r["Name"].rstrip("."), "type": r["Type"]}
                    for r in dns_conflicts
                ]
            }

        run_command(
            [terraform, "apply", "-auto-approve", f"-var-file={tfvars_path}"],
            cwd=TERRAFORM_DIR,
            env=env,
        )

        return read_terraform_outputs(env=env)
    finally:
        os.close(fd)
        Path(tfvars_path).unlink(missing_ok=True)


def wait_with_spinner(
    message: str,
    check_fn,
    timeout: int = 600,
    on_tick: Callable[[], list[str]] | None = None,
) -> None:
    """Wait for a condition with a spinner.

    ``on_tick``, if given, is polled every iteration and any lines it returns
    are written as progress output before the spinner redraws — used to tail
    remote logs so long waits aren't silent.
    """
    spinner = ["|", "/", "-", "\\"]
    idx = 0
    start = time.time()

    while True:
        if on_tick:
            for line in on_tick():
                write_line(f"[deploy]   {line}", detail=True)

        elapsed = int(time.time() - start)
        write_inline(f"[deploy] {message} {spinner[idx % len(spinner)]} ({elapsed}s)")

        if check_fn():
            write_line(f"[deploy] {message} ✓")
            return

        if elapsed > timeout:
            write_line("")
            raise DeployError(f"timeout waiting for: {message}")

        time.sleep(1)
        idx += 1


def wait_for_ssm_online(
    instance_id: str, region: str, session: boto3.Session | None = None
) -> None:
    """Wait for SSM to become available on the instance."""
    ssm = _client(session, "ssm", region)

    def check():
        response = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        for item in response.get("InstanceInformationList", []):
            if item.get("PingStatus") == "Online":
                return True
        return False

    wait_with_spinner("Waiting for SSM online", check, timeout=300)


def wait_for_runner_bootstrap_completion(
    instance_id: str, region: str, session: boto3.Session | None = None
) -> None:
    """Wait for the initial cloud-init bootstrap to finish."""
    run_ssm_command(
        instance_id,
        region,
        ["timeout 300 bash -c 'while [ ! -f /opt/glow-runner/bootstrap.ready ]; do sleep 1; done'"],
        "wait for runner bootstrap completion",
        session=session,
    )


def _send_ssm_command_and_wait(
    instance_id: str,
    region: str,
    commands: list[str],
    comment: str,
    timeout: int,
    session: boto3.Session | None,
    on_tick: Callable[[], list[str]] | None = None,
) -> str:
    """Send an SSM command, wait for completion, and return its stdout.

    Shared by ``run_ssm_command`` (which discards the output) and
    ``run_ssm_command_capturing_output`` (which returns it) so the
    invocation-polling logic isn't duplicated between them.
    """
    from botocore.exceptions import ClientError

    ssm = _client(session, "ssm", region)

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment=comment,
        TimeoutSeconds=timeout,
        Parameters={"commands": commands},
    )

    command_id = response["Command"]["CommandId"]
    result: dict[str, str] = {}

    def check():
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "InvocationDoesNotExist":
                return False
            raise

        status = invocation.get("Status", "Unknown")

        if status == "Success" and int(invocation.get("ResponseCode", 1)) == 0:
            result["output"] = invocation.get("StandardOutputContent", "")
            return True

        if status in {"Cancelled", "TimedOut", "Failed", "Cancelling"}:
            stderr = invocation.get("StandardErrorContent", "").strip()
            stdout = invocation.get("StandardOutputContent", "").strip()
            details = stderr or stdout or status
            raise DeployError(f"remote command failed: {details}")

        return False

    wait_with_spinner(comment, check, timeout=timeout, on_tick=on_tick)
    return result.get("output", "")


def run_ssm_command(
    instance_id: str,
    region: str,
    commands: list[str],
    comment: str,
    timeout: int = 1800,
    session: boto3.Session | None = None,
    on_tick: Callable[[], list[str]] | None = None,
) -> None:
    """Run a command via SSM and wait for completion, discarding its output."""
    _send_ssm_command_and_wait(instance_id, region, commands, comment, timeout, session, on_tick)


def run_ssm_command_capturing_output(
    instance_id: str,
    region: str,
    commands: list[str],
    comment: str,
    timeout: int = 300,
    session: boto3.Session | None = None,
) -> str:
    """Run a command via SSM and return its stdout.

    For read-only status/log checks (health, deployed git ref) where the
    caller wants the remote command's output, not just success/failure.
    """
    return _send_ssm_command_and_wait(
        instance_id, region, commands, comment, timeout, session
    )


def prepare_runner_repository(
    instance_id: str,
    region: str,
    repo_url: str,
    checkout_ref: str,
    session: boto3.Session | None = None,
) -> None:
    """Ensure the runner has the requested repository checkout."""
    script = f"""set -euo pipefail
repo_url={shlex.quote(repo_url)}
checkout_ref={shlex.quote(checkout_ref)}

if [[ -d /opt/glow/.git ]]; then
  current_origin="$(git -C /opt/glow remote get-url origin || true)"
  if [[ "${{current_origin}}" != "${{repo_url}}" ]]; then
    rm -rf /opt/glow
  fi
else
  rm -rf /opt/glow
fi

if [[ ! -d /opt/glow/.git ]]; then
  git clone "${{repo_url}}" /opt/glow
fi

git -C /opt/glow fetch --tags --prune origin
git -C /opt/glow checkout --force "${{checkout_ref}}"
"""

    run_ssm_command(
        instance_id,
        region,
        [f"sudo bash -lc {shlex.quote(script)}"],
        "prepare runner repository",
        timeout=3600,
        session=session,
    )


_CLOUDWATCH_NOISE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^[IDWE]! ",  # amazon-cloudwatch-agent-ctl's own log lines
        r"^/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent ",
        r"^Configuration validation",
    )
)


def _tail_new_cloudwatch_lines(
    logs_client, log_group: str, stream_name: str
) -> Callable[[], list[str]]:
    """Build a poll() callable that returns lines appended to a CloudWatch log
    stream since this closure was built — a stream reused across deploys (same
    instance, same log group/stream name) already has history from previous
    runs, so a plain "first call" cutoff would replay that stale tail as if it
    were new. Filtering by wall-clock start time avoids that regardless of
    what the stream already contains. Also drops known agent-startup noise
    (the amazon-cloudwatch-agent-ctl schema-validation banner, printed once
    per ``-s`` restart in the userdata script) so it isn't shown twice.
    """
    from botocore.exceptions import ClientError

    state: dict[str, Any] = {"token": None, "start_time_ms": int(time.time() * 1000) - 5000}

    def poll() -> list[str]:
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "logStreamName": stream_name,
            "startFromHead": True,
        }
        if state["token"] is None:
            kwargs["startTime"] = state["start_time_ms"]
        else:
            kwargs["nextToken"] = state["token"]

        try:
            response = logs_client.get_log_events(**kwargs)
        except ClientError:
            return []

        state["token"] = response.get("nextForwardToken")
        return [
            event["message"]
            for event in response.get("events", [])
            if not any(pattern.search(event["message"]) for pattern in _CLOUDWATCH_NOISE_PATTERNS)
        ]

    return poll


def rerun_runner_userdata(
    instance_id: str,
    region: str,
    domain_name: str,
    env: dict[str, str] | None = None,
    session: boto3.Session | None = None,
) -> None:
    """Rerun the instance userdata script via SSM.

    ``env`` here is a set of environment variables exported inside the remote
    shell script, unrelated to the local subprocess environment used for
    terraform/packer. While waiting for the command to finish, tails the
    bootstrap log's CloudWatch stream so the (often multi-minute) run isn't
    silent.
    """
    env_exports = "\n".join(
        f"export {name}={shlex.quote(value)}" for name, value in (env or {}).items()
    )
    script = f"""set -euo pipefail
{env_exports}
if test -f /var/lib/cloud/instance/user-data.txt; then
  userdata_script=/var/lib/cloud/instance/user-data.txt
elif test -f /var/lib/cloud/instance/scripts/part-001; then
  userdata_script=/var/lib/cloud/instance/scripts/part-001
else
  echo 'userdata script not found' >&2
  exit 1
fi

 bash \"${{userdata_script}}\" || {{
  status=$?
  last_log_line=\"$(tail -n 1 /var/log/glow-runner-bootstrap.log 2>/dev/null || true)\"
  if test -n \"${{last_log_line}}\"; then
    echo \"last bootstrap log line: ${{last_log_line}}\" >&2
  fi
  exit \"${{status}}\"
}}
"""

    logs_client = _client(session, "logs", region)
    on_tick = _tail_new_cloudwatch_lines(
        logs_client,
        f"/glow/{domain_name}/bootstrap",
        f"{instance_id}/runner-bootstrap",
    )

    run_ssm_command(
        instance_id,
        region,
        [f"sudo bash -lc {shlex.quote(script)}"],
        "rerun runner userdata",
        timeout=3600,
        session=session,
        on_tick=on_tick,
    )


def verify_runner_health(
    instance_id: str, region: str, session: boto3.Session | None = None
) -> None:
    """Verify the runner services are healthy."""
    run_ssm_command(
        instance_id,
        region,
        ["sudo /opt/glow-runner/healthcheck.sh"],
        "verify runner health",
        session=session,
    )


def get_runner_status(
    instance_id: str, region: str, session: boto3.Session | None = None
) -> dict[str, str]:
    """Read health and deployed git ref/commit for the GUI's status/logs view.

    Reuses the same on-instance scripts ``verify_runner_health`` and the CLI's
    update flow already depend on (``healthcheck.sh``, ``get-git-ref.sh``) —
    no CloudWatch Logs integration, just what's already on the box.
    """
    health = run_ssm_command_capturing_output(
        instance_id,
        region,
        ["sudo /opt/glow-runner/healthcheck.sh"],
        "check runner health",
        session=session,
    )
    # get-git-ref.sh isn't baked into the AMI; it lives in the git checkout
    # that prepare_runner_repository() clones to /opt/glow, at the same
    # repo-relative path it has locally.
    get_git_ref = "/opt/glow/deploy/aws/runtime/get-git-ref.sh"
    git_ref = run_ssm_command_capturing_output(
        instance_id,
        region,
        [f"{get_git_ref} --ref"],
        "read deployed git ref",
        session=session,
    )
    git_commit = run_ssm_command_capturing_output(
        instance_id,
        region,
        [f"{get_git_ref} --commit"],
        "read deployed git commit",
        session=session,
    )
    return {
        "health": health.strip(),
        "git_ref": git_ref.strip(),
        "git_commit": git_commit.strip(),
    }


def get_deployed_version(domain_name: str, timeout: float = 5.0) -> str | None:
    """Live version reported by the deployed API's own root endpoint, or None
    if unreachable/malformed. Doubles as a lightweight health signal — no
    response means something more important than a version mismatch."""
    try:
        response = httpx.get(f"https://api.{domain_name}/", timeout=timeout)
        response.raise_for_status()
        return response.json().get("version")
    except Exception:
        return None


def get_container_logs(
    instance_id: str,
    domain_name: str,
    region: str,
    session: boto3.Session | None = None,
    max_lines: int = 200,
) -> dict[str, list[str]]:
    """Fetch recent container logs from CloudWatch, grouped by container name.

    Containers ship logs to the group terraform creates at
    ``/glow/<domain>/containers``, one log stream per container named
    ``<instance_id>-<container_name>`` (the docker awslogs "tag" option in
    runner-userdata.sh.tpl). Filters to this instance's streams and strips
    the instance-id prefix to recover the container name.
    """
    from botocore.exceptions import ClientError

    logs_client = _client(session, "logs", region)
    log_group = f"/glow/{domain_name}/containers"
    prefix = f"{instance_id}-"

    try:
        streams = []
        paginator = logs_client.get_paginator("describe_log_streams")
        for page in paginator.paginate(logGroupName=log_group, logStreamNamePrefix=prefix):
            streams.extend(page["logStreams"])
    except ClientError as exc:
        raise DeployError(f"couldn't list container log streams: {exc}") from exc

    result: dict[str, list[str]] = {}
    for stream in streams:
        stream_name = stream["logStreamName"]
        container = stream_name[len(prefix):]
        result[container] = _get_log_stream_messages(logs_client, log_group, stream_name, max_lines)
    return result


def get_container_log_tail(
    instance_id: str,
    domain_name: str,
    container_name: str,
    region: str,
    session: boto3.Session | None = None,
    max_lines: int = 200,
) -> list[str]:
    """Fetch the most recent lines from a single container's log stream.

    For the GUI's per-container "tail" polling — cheaper than
    get_container_logs since it skips the describe_log_streams call, going
    straight to the deterministic stream name.
    """
    logs_client = _client(session, "logs", region)
    log_group = f"/glow/{domain_name}/containers"
    stream_name = f"{instance_id}-{container_name}"
    return _get_log_stream_messages(logs_client, log_group, stream_name, max_lines)


def _get_log_stream_messages(logs_client, log_group: str, stream_name: str, max_lines: int) -> list[str]:
    from botocore.exceptions import ClientError

    try:
        events = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=stream_name,
            limit=max_lines,
            startFromHead=False,
        )["events"]
    except ClientError as exc:
        raise DeployError(f"couldn't read logs for stream {stream_name}: {exc}") from exc
    return [event["message"] for event in events]


def verify_alb_routing(alb_dns: str, domain_name: str) -> None:
    """Verify ALB routing works with Host headers."""
    import http.client

    write_line("[deploy] Verifying ALB routing")

    endpoints = [
        (domain_name, "/en", "Dashboard"),
        (f"api.{domain_name}", "/health", "API"),
        (f"odk.{domain_name}", "/", "ODK"),
    ]

    for host, path, name in endpoints:
        conn = http.client.HTTPSConnection(alb_dns, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": host})
            response = conn.getresponse()
            if response.status in (200, 301, 302):
                write_line(f"[deploy]   {name} routing ✓")
            else:
                raise DeployError(f"{name} routing failed: HTTP {response.status}")
        finally:
            conn.close()


def list_deployments(
    session: boto3.Session | None = None, region: str = "eu-west-2"
) -> list[dict[str, Any]]:
    """List runner instances managed by this tool, via the tags Terraform sets.

    Reads the ``Component``, ``Domain``, ``GitRef`` and ``GitCommit`` tags that
    ``terraform/runner.tf`` and ``terraform/main.tf`` already apply to every
    runner instance — no new Terraform and no local state file needed.
    """
    ec2 = _client(session, "ec2", region)
    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Component", "Values": ["glow-runner"]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    )

    deployments = []
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
            deployments.append(
                {
                    "instance_id": instance["InstanceId"],
                    "state": instance["State"]["Name"],
                    "domain": tags.get("Domain"),
                    "git_ref": tags.get("GitRef"),
                    "git_commit": tags.get("GitCommit"),
                    "launch_time": instance.get("LaunchTime"),
                }
            )
    return deployments


def get_cpu_utilization(
    instance_ids: list[str], region: str, session: boto3.Session | None = None
) -> dict[str, float | None]:
    """Average CPU utilization over the last 15 minutes, per instance.

    Uses EC2's built-in CloudWatch metric (no agent install needed) as a
    cheap proxy for both current load and recent activity.
    """
    if not instance_ids:
        return {}

    from datetime import datetime, timedelta, timezone

    cloudwatch = _client(session, "cloudwatch", region)
    now = datetime.now(timezone.utc)
    try:
        response = cloudwatch.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": f"cpu{i}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/EC2",
                            "MetricName": "CPUUtilization",
                            "Dimensions": [{"Name": "InstanceId", "Value": iid}],
                        },
                        "Period": 900,
                        "Stat": "Average",
                    },
                }
                for i, iid in enumerate(instance_ids)
            ],
            StartTime=now - timedelta(minutes=15),
            EndTime=now,
        )
    except Exception:
        return {iid: None for iid in instance_ids}

    results = {r["Id"]: r["Values"][0] if r["Values"] else None for r in response["MetricDataResults"]}
    return {iid: results.get(f"cpu{i}") for i, iid in enumerate(instance_ids)}


def provision(config: Config) -> dict[str, Any] | None:
    """Initial provision: build AMI, apply Terraform, activate stack."""
    if config.dry_run:
        write_line("Checking configuration with a dry run.")
    write_line(f"[deploy] Provisioning {config.domain_name}")
    write_line(f"[deploy] Git reference: {config.git_ref} ({config.git_commit[:8]})")

    bucket = ensure_state_bucket(
        config.aws_region, config.domain_name, config.session
    )

    ami_id = (
        None
        if config.force_rebuild_ami
        else find_ami_in_account(
            config.aws_region, config.git_commit, config.session
        )
    )

    if ami_id:
        write_line(f"[deploy] Using existing AMI: {ami_id}")
    else:
        ami_id = build_ami_with_packer(
            config.aws_region, config.git_commit, config.session
        )
        write_line(f"[deploy] Built AMI: {ami_id}")

    terraform_init(bucket, config.aws_region, config.session)

    write_line("[deploy] Applying Terraform")
    outputs = terraform_apply(config, ami_id)

    if config.dry_run:
        write_line("[deploy] Dry-run complete")
        return outputs

    instance_id = outputs["runner_instance_id"]
    alb_dns = outputs["alb_dns_name"]

    wait_for_ssm_online(instance_id, config.aws_region, config.session)
    wait_for_runner_bootstrap_completion(
        instance_id, config.aws_region, config.session
    )
    prepare_runner_repository(
        instance_id,
        config.aws_region,
        config.git_repo_url,
        config.git_commit,
        config.session,
    )
    rerun_runner_userdata(
        instance_id,
        config.aws_region,
        config.domain_name,
        {
            "GIT_REPO_URL": config.git_repo_url,
            "GIT_REF": config.git_ref,
            "GIT_COMMIT": config.git_commit,
        },
        config.session,
    )
    verify_runner_health(instance_id, config.aws_region, config.session)

    write_line("[deploy] Deployment complete!")
    write_line(f"[deploy] Instance ID: {instance_id}")
    write_line(f"[deploy] ALB DNS: {alb_dns}")
    write_line(f"[deploy] Dashboard: https://{config.domain_name}")
    write_line(f"[deploy] API: https://api.{config.domain_name}")
    write_line(f"[deploy] ODK: https://odk.{config.domain_name}")


def update(config: Config) -> None:
    """Update existing instance via SSM."""
    if config.dry_run:
        write_line("Checking configuration with a dry run.")
    write_line(f"[deploy] Updating to {config.git_ref} ({config.git_commit[:8]})")

    bucket = ensure_state_bucket(
        config.aws_region, config.domain_name, config.session
    )
    terraform_init(bucket, config.aws_region, config.session)

    outputs = read_terraform_outputs(env=_subprocess_env(config.session))
    instance_id = outputs["runner_instance_id"]

    if config.dry_run:
        write_line(
            f"[deploy] Would update instance {instance_id} to "
            f"{config.git_ref} ({config.git_commit})"
        )
        return

    wait_for_ssm_online(instance_id, config.aws_region, config.session)
    wait_for_runner_bootstrap_completion(
        instance_id, config.aws_region, config.session
    )

    prepare_runner_repository(
        instance_id,
        config.aws_region,
        config.git_repo_url,
        config.git_commit,
        config.session,
    )
    rerun_runner_userdata(
        instance_id,
        config.aws_region,
        config.domain_name,
        {
            "GIT_REPO_URL": config.git_repo_url,
            "GIT_REF": config.git_ref,
            "GIT_COMMIT": config.git_commit,
        },
        config.session,
    )
    verify_runner_health(instance_id, config.aws_region, config.session)

    # ponytail: a future `terraform apply` on this deployment reverts these
    # tags to Terraform state's original values — known ceiling, not solved
    # here. They're also no longer the primary "what version is running"
    # source (that's the live API via get_deployed_version); this just keeps
    # them from going stale as the fallback display.
    ec2 = _client(config.session, "ec2", config.aws_region)
    ec2.create_tags(
        Resources=[instance_id],
        Tags=[
            {"Key": "GitRef", "Value": config.git_ref},
            {"Key": "GitCommit", "Value": config.git_commit},
        ],
    )

    write_line("[deploy] Update complete!")


def destroy(config: Config) -> None:
    """Tear down all Terraform-managed infrastructure for a deployment.

    app_name/runner_instance_type/runner_root_volume_size_gb/runner_ami_id
    only affect resource naming and tags here, not resource identity, so a
    destroy plan deletes the real resources by their state addresses
    regardless of these placeholder values. hosted_zone_id/certificate_arn
    do gate which resources exist in config (the auto-DNS vs. pasted-cert
    path), but ``terraform destroy`` tears down everything already in state
    regardless of what the current config would create, so placeholders are
    safe here too.
    """
    write_line(f"[deploy] Destroying {config.domain_name}")

    bucket = ensure_state_bucket(
        config.aws_region, config.domain_name, config.session
    )
    terraform_init(bucket, config.aws_region, config.session)

    env = _subprocess_env(config.session)

    tfvars = {
        "app_name": config.app_name,
        "aws_region": config.aws_region,
        "hosted_zone_id": "",
        "certificate_arn": "",
        "domain_name": config.domain_name,
        "git_ref": config.git_ref,
        "git_repo_url": config.git_repo_url,
        "git_checkout_ref": config.git_commit,
        "runner_ami_id": "ami-00000000000000000",
        "runner_instance_type": config.runner_instance_type,
        "runner_root_volume_size_gb": config.runner_root_volume_size_gb,
    }

    fd, tfvars_path = tempfile.mkstemp(suffix=".tfvars.json")
    terraform = binaries.terraform_binary()
    try:
        Path(tfvars_path).write_text(json.dumps(tfvars, indent=2))

        if config.dry_run:
            result = run_command(
                [terraform, "plan", "-destroy", f"-var-file={tfvars_path}"],
                cwd=TERRAFORM_DIR,
                env=env,
            )
            write_line(result.stdout)
            return

        run_command(
            [terraform, "destroy", "-auto-approve", f"-var-file={tfvars_path}"],
            cwd=TERRAFORM_DIR,
            env=env,
        )
    finally:
        os.close(fd)
        Path(tfvars_path).unlink(missing_ok=True)

    write_line(f"[deploy] {config.domain_name} destroyed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, dest="domain_name")
    parser.add_argument(
        "--certificate-arn",
        default="",
        help=(
            "Existing ACM certificate ARN, required only when this AWS "
            "account has no Route 53 hosted zone for the domain (or a "
            "parent of it)"
        ),
    )
    parser.add_argument("--git-ref", default="")
    parser.add_argument("--git-repo-url", default=DEFAULT_GIT_REPO_URL)
    parser.add_argument(
        "--aws-region", default=os.environ.get("AWS_REGION", "eu-west-2")
    )
    parser.add_argument("--app-name", default="glow-core")
    parser.add_argument("--runner-instance-type", default="t3.medium")
    parser.add_argument("--runner-root-volume-size-gb", type=int, default=100)
    parser.add_argument("--force-rebuild-ami", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing instance instead of provision",
    )

    args = parser.parse_args()

    try:
        # Fail fast if terraform/packer aren't resolvable, rather than partway
        # through a multi-minute provision/update run.
        binaries.terraform_binary()
        binaries.packer_binary()

        git_ref = args.git_ref or "main"
        git_commit = github_api.resolve_git_commit_via_github(
            args.git_repo_url, git_ref
        )

        config = Config(
            domain_name=args.domain_name,
            certificate_arn=args.certificate_arn,
            git_repo_url=args.git_repo_url,
            git_ref=git_ref,
            git_commit=git_commit,
            aws_region=args.aws_region,
            app_name=args.app_name,
            runner_instance_type=args.runner_instance_type,
            runner_root_volume_size_gb=args.runner_root_volume_size_gb,
            dry_run=args.dry_run,
            force_rebuild_ami=args.force_rebuild_ami,
        )

        if args.update:
            update(config)
        else:
            provision(config)

        return 0

    except DeployError as exc:
        write_line(f"[deploy] ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        write_line("\n[deploy] Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
