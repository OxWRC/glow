# EBS Snapshot Lifecycle for glow-deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire EBS snapshots into glow-deploy's `update()` and `destroy()` paths (snapshot-before-mutate, snapshot-then-delete-volume), and make snapshots visible/manageable (list, delete, restore-into-a-new-deployment) from the GUI.

**Architecture:** Five new functions in `core.py` (`find_root_volume_id`, `create_snapshot`, `list_snapshots`, `delete_snapshot`, `restore_snapshot_data`) built on the existing `_client`/`write_line`/`wait_with_spinner` conventions. `update()` and `destroy()` each gain one hook call. `provision()` gains one conditional final step gated by a new `Config.restore_from_snapshot_id` field. The GUI adds a snapshots card to the deployment detail page, a new global `/snapshots` page, and a snapshot-picker dropdown on the new-deployment form — no new job kind, snapshot delete is a synchronous route (fast boto3 call, no background job needed).

**Tech Stack:** Python 3, boto3 (EC2), FastAPI + Jinja2 templates, pytest + monkeypatch.

**Spec:** `docs/superpowers/specs/2026-08-24-ebs-snapshots-design.md`

## Global Constraints

- Snapshot tags, exactly: `Domain=<domain>`, `Component=glow-runner-snapshot`, `Reason=pre-update|pre-destroy`.
- Restore is **data-only**: copy `glow-postgres`/`odk-postgres` out of a snapshot into a fresh, already-provisioned instance. Never swap the root volume.
- No automatic retention/expiry — snapshots persist until a human deletes them via the GUI.
- No on-demand "snapshot now" button in v1 — only the two automatic triggers (pre-update, pre-destroy).
- No cross-region/cross-account snapshot copy.
- `list_snapshots(region, session, domain=None)` — omitting `domain` must return the global list, including snapshots whose domain no longer has a live deployment (orphans).
- All new AWS calls go through the existing `_client(session, service_name, region)` helper (`core.py:120`).

---

### Task 1: Tag the root EBS volume in Terraform

**Files:**
- Modify: `deploy/aws/terraform/runner.tf:35-79` (the `aws_instance.runner` resource)

**Interfaces:**
- Produces: the root volume itself now carries `Domain`/`Component=glow-runner`/`ManagedBy`/`Stack` tags (previously only the instance carried tags) — this is what lets `find_root_volume_id` (Task 2) locate "this deployment's volume" without threading a volume ID through `Config`.

Today only the instance is tagged (`runner.tf:69-74`); the root EBS volume itself carries no tags. Add a top-level `volume_tags` argument to `aws_instance.runner` (applies to every EBS volume attached at launch — here, just the root volume) using the same `local.tags` map instance tags already merge from (`terraform/main.tf:5-11`: `ManagedBy`, `project-name`, `Domain`, `Stack`).

- [x] **Step 1: Add `volume_tags` to the runner instance**

In `deploy/aws/terraform/runner.tf`, immediately after the `tags = merge(...)` block (ends at line 74) and before the `lifecycle` block (line 76), add:

```hcl
  volume_tags = merge(local.tags, {
    Name      = "${var.app_name}-runner-root"
    Component = "glow-runner"
  })
```

So the resource reads:

```hcl
  tags = merge(local.tags, {
    Name      = "${var.app_name}-runner"
    Component = "glow-runner"
    GitRef    = var.git_ref
    GitCommit = var.git_checkout_ref
  })

  volume_tags = merge(local.tags, {
    Name      = "${var.app_name}-runner-root"
    Component = "glow-runner"
  })

  lifecycle {
    ignore_changes = [ami, user_data]
  }
```

- [x] **Step 2: Validate the Terraform syntax**

Run: `cd deploy/aws/terraform && terraform fmt -check -diff && terraform validate`

(If there's no initialized backend in this environment, `terraform validate` still works without `init` for syntax/type checking; if it complains about missing providers, run `terraform init -backend=false` first, then `terraform validate`.)

Expected: no diff from `fmt`, and `Success! The configuration is valid.` from `validate`.

- [x] **Step 3: Commit**

```bash
git add deploy/aws/terraform/runner.tf
git commit -m "feat: tag the runner's root EBS volume with Domain/Component"
```

---

### Task 2: Snapshot CRUD primitives in core.py

**Files:**
- Modify: `deploy/aws/src/glow_deploy/core.py` (add new functions after `list_deployments`, i.e. after line 969)
- Test: `deploy/aws/tests/test_core.py`

**Interfaces:**
- Produces:
  - `find_root_volume_id(instance_id: str, region: str, session: boto3.Session | None = None) -> str`
  - `create_snapshot(volume_id: str, domain: str, reason: str, region: str, session: boto3.Session | None = None) -> str`
  - `list_snapshots(region: str, session: boto3.Session | None = None, domain: str | None = None) -> list[dict[str, Any]]` — each dict: `snapshot_id, domain, reason, started_at, size_gb, state`
  - `delete_snapshot(snapshot_id: str, region: str, session: boto3.Session | None = None) -> None`
- Consumes: `_client` (`core.py:120`), `write_line` (`core.py:96`), `wait_with_spinner` (`core.py:468`), `DeployError` (`glow_deploy.errors`).

- [x] **Step 1: Write the failing tests**

Add to `deploy/aws/tests/test_core.py`, after the existing `_FakeEc2Client` class (line 195) — extend it with the new methods these functions need, and add the tests below (place them near `test_list_deployments_maps_tags_from_terraform_managed_instances`, e.g. right after it, before `class _FakeRoute53Client` at line 239):

```python
class _FakeEc2ClientForSnapshots:
    def __init__(self):
        self.describe_instances_response = None
        self.create_snapshot_calls: list[dict] = []
        self.describe_snapshots_calls: list[dict] = []
        self.describe_snapshots_response = {"Snapshots": []}
        self.delete_snapshot_calls: list[dict] = []
        self._snapshot_state = "completed"

    def describe_instances(self, **kwargs):
        return self.describe_instances_response

    def create_snapshot(self, **kwargs):
        self.create_snapshot_calls.append(kwargs)
        return {"SnapshotId": "snap-1234567890"}

    def describe_snapshots(self, **kwargs):
        self.describe_snapshots_calls.append(kwargs)
        if kwargs.get("SnapshotIds"):
            return {
                "Snapshots": [
                    {"SnapshotId": kwargs["SnapshotIds"][0], "State": self._snapshot_state}
                ]
            }
        return self.describe_snapshots_response

    def delete_snapshot(self, **kwargs):
        self.delete_snapshot_calls.append(kwargs)


def test_find_root_volume_id_reads_root_device_mapping(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    fake_ec2.describe_instances_response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-1234567890",
                        "RootDeviceName": "/dev/xvda",
                        "BlockDeviceMappings": [
                            {"DeviceName": "/dev/xvda", "Ebs": {"VolumeId": "vol-abc123"}},
                            {"DeviceName": "/dev/xvdf", "Ebs": {"VolumeId": "vol-other"}},
                        ],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    volume_id = core.find_root_volume_id("i-1234567890", "eu-west-2", session=None)

    assert volume_id == "vol-abc123"


def test_find_root_volume_id_raises_when_root_mapping_missing(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    fake_ec2.describe_instances_response = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-1234567890",
                        "RootDeviceName": "/dev/xvda",
                        "BlockDeviceMappings": [],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    with pytest.raises(core.DeployError):
        core.find_root_volume_id("i-1234567890", "eu-west-2", session=None)


def test_create_snapshot_tags_and_waits_for_completion(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    snapshot_id = core.create_snapshot(
        "vol-abc123", "eu.glow-project.org", "pre-update", "eu-west-2", session=None
    )

    assert snapshot_id == "snap-1234567890"
    assert fake_ec2.create_snapshot_calls[0]["VolumeId"] == "vol-abc123"
    tag_spec = fake_ec2.create_snapshot_calls[0]["TagSpecifications"][0]
    assert tag_spec["ResourceType"] == "snapshot"
    assert {"Key": "Domain", "Value": "eu.glow-project.org"} in tag_spec["Tags"]
    assert {"Key": "Component", "Value": "glow-runner-snapshot"} in tag_spec["Tags"]
    assert {"Key": "Reason", "Value": "pre-update"} in tag_spec["Tags"]
    assert fake_ec2.describe_snapshots_calls[0]["SnapshotIds"] == ["snap-1234567890"]


def test_list_snapshots_maps_tags_and_filters_by_domain(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    fake_ec2.describe_snapshots_response = {
        "Snapshots": [
            {
                "SnapshotId": "snap-1",
                "StartTime": "2026-01-01T00:00:00Z",
                "VolumeSize": 100,
                "State": "completed",
                "Tags": [
                    {"Key": "Domain", "Value": "eu.glow-project.org"},
                    {"Key": "Component", "Value": "glow-runner-snapshot"},
                    {"Key": "Reason", "Value": "pre-update"},
                ],
            }
        ]
    }
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    snapshots = core.list_snapshots("eu-west-2", session=None, domain="eu.glow-project.org")

    assert snapshots == [
        {
            "snapshot_id": "snap-1",
            "domain": "eu.glow-project.org",
            "reason": "pre-update",
            "started_at": "2026-01-01T00:00:00Z",
            "size_gb": 100,
            "state": "completed",
        }
    ]
    filters = fake_ec2.describe_snapshots_calls[0]["Filters"]
    assert {"Name": "tag:Component", "Values": ["glow-runner-snapshot"]} in filters
    assert {"Name": "tag:Domain", "Values": ["eu.glow-project.org"]} in filters


def test_list_snapshots_without_domain_returns_global_list(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    core.list_snapshots("eu-west-2", session=None)

    filters = fake_ec2.describe_snapshots_calls[0]["Filters"]
    assert all(f["Name"] != "tag:Domain" for f in filters)


def test_delete_snapshot_calls_ec2(monkeypatch):
    fake_ec2 = _FakeEc2ClientForSnapshots()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    core.delete_snapshot("snap-1234567890", "eu-west-2", session=None)

    assert fake_ec2.delete_snapshot_calls == [{"SnapshotId": "snap-1234567890"}]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "root_volume_id or create_snapshot or list_snapshots or delete_snapshot" -v`
Expected: FAIL with `AttributeError: module 'glow_deploy.core' has no attribute 'find_root_volume_id'` (and similarly for the other three).

- [x] **Step 3: Implement the four functions**

In `deploy/aws/src/glow_deploy/core.py`, add after `list_deployments` (after line 969, before `get_cpu_utilization`):

```python
def find_root_volume_id(
    instance_id: str, region: str, session: boto3.Session | None = None
) -> str:
    """Find the EBS volume ID backing an instance's root device.

    Must be called while the instance is still running/attached — both
    ``update()``'s pre-update hook and ``destroy()``'s pre-destroy hook need
    this before the instance terminates and the volume becomes hard to find.
    """
    ec2 = _client(session, "ec2", region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response["Reservations"][0]["Instances"][0]
    root_device_name = instance["RootDeviceName"]
    for mapping in instance.get("BlockDeviceMappings", []):
        if mapping["DeviceName"] == root_device_name:
            return mapping["Ebs"]["VolumeId"]
    raise DeployError(f"no root volume found for instance {instance_id}")


def create_snapshot(
    volume_id: str,
    domain: str,
    reason: str,
    region: str,
    session: boto3.Session | None = None,
) -> str:
    """Snapshot an EBS volume, tag it, and wait for the snapshot to complete.

    Waits for completion rather than returning immediately so a caller that
    deletes the source volume right after (``destroy()``'s pre-destroy hook)
    never races an in-progress snapshot against the volume disappearing.
    """
    ec2 = _client(session, "ec2", region)
    write_line(f"[deploy] Snapshotting volume {volume_id} ({reason})")
    response = ec2.create_snapshot(
        VolumeId=volume_id,
        Description=f"glow-deploy {reason} snapshot for {domain}",
        TagSpecifications=[
            {
                "ResourceType": "snapshot",
                "Tags": [
                    {"Key": "Domain", "Value": domain},
                    {"Key": "Component", "Value": "glow-runner-snapshot"},
                    {"Key": "Reason", "Value": reason},
                ],
            }
        ],
    )
    snapshot_id = response["SnapshotId"]

    def check() -> bool:
        state = ec2.describe_snapshots(SnapshotIds=[snapshot_id])["Snapshots"][0]["State"]
        return state == "completed"

    wait_with_spinner(f"Waiting for snapshot {snapshot_id}", check, timeout=1800)
    write_line(f"[deploy] Snapshot {snapshot_id} complete")
    return snapshot_id


def list_snapshots(
    region: str, session: boto3.Session | None = None, domain: str | None = None
) -> list[dict[str, Any]]:
    """List glow-runner snapshots, optionally scoped to one domain.

    Omitting ``domain`` returns every snapshot this tool created, including
    ones tagged for a domain that no longer has a live deployment — the
    snapshot carries its own Domain tag independent of the instance.
    """
    ec2 = _client(session, "ec2", region)
    filters = [{"Name": "tag:Component", "Values": ["glow-runner-snapshot"]}]
    if domain:
        filters.append({"Name": "tag:Domain", "Values": [domain]})
    response = ec2.describe_snapshots(Filters=filters, OwnerIds=["self"])

    snapshots = []
    for snap in response.get("Snapshots", []):
        tags = {tag["Key"]: tag["Value"] for tag in snap.get("Tags", [])}
        snapshots.append(
            {
                "snapshot_id": snap["SnapshotId"],
                "domain": tags.get("Domain"),
                "reason": tags.get("Reason"),
                "started_at": snap.get("StartTime"),
                "size_gb": snap.get("VolumeSize"),
                "state": snap.get("State"),
            }
        )
    return snapshots


def delete_snapshot(
    snapshot_id: str, region: str, session: boto3.Session | None = None
) -> None:
    ec2 = _client(session, "ec2", region)
    ec2.delete_snapshot(SnapshotId=snapshot_id)
    write_line(f"[deploy] Deleted snapshot {snapshot_id}")
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "root_volume_id or create_snapshot or list_snapshots or delete_snapshot" -v`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add deploy/aws/src/glow_deploy/core.py deploy/aws/tests/test_core.py
git commit -m "feat: add EBS snapshot CRUD primitives to core.py"
```

---

### Task 3: Snapshot the volume before `update()` mutates the instance

**Files:**
- Modify: `deploy/aws/src/glow_deploy/core.py:1083-1141` (`update()`)
- Test: `deploy/aws/tests/test_core.py`

**Interfaces:**
- Consumes: `find_root_volume_id`, `create_snapshot` (Task 2).

`prepare_runner_repository` (the first mutating call in `update()` — it runs `git checkout --force`) must not run until the pre-update snapshot has completed. Insert the hook right after `wait_for_runner_bootstrap_completion` and before `prepare_runner_repository`.

- [x] **Step 1: Write the failing test**

Add to `deploy/aws/tests/test_core.py`, right after `test_update_prepares_repository_before_rerunning_userdata` (after line 858, before `test_provision_prepares_repository_before_rerunning_userdata`):

```python
def test_update_snapshots_volume_before_preparing_repository(monkeypatch):
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
        "find_root_volume_id",
        lambda instance_id, region, session=None: calls.append(("find_volume", instance_id))
        or "vol-abc123",
    )
    monkeypatch.setattr(
        core,
        "create_snapshot",
        lambda volume_id, domain, reason, region, session=None: calls.append(
            ("snapshot", volume_id, domain, reason)
        )
        or "snap-1234567890",
    )
    monkeypatch.setattr(
        core,
        "prepare_runner_repository",
        lambda instance_id, region, repo_url, checkout_ref, session=None: calls.append(
            ("prepare", instance_id)
        ),
    )
    monkeypatch.setattr(
        core,
        "rerun_runner_userdata",
        lambda instance_id, region, domain_name, env=None, session=None: calls.append(
            ("rerun", instance_id)
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
        ("find_volume", "i-1234567890"),
        ("snapshot", "vol-abc123", "example.com", "pre-update"),
        ("prepare", "i-1234567890"),
        ("rerun", "i-1234567890"),
        ("verify", "i-1234567890"),
    ]
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k test_update_snapshots_volume_before_preparing_repository -v`
Expected: FAIL — the `calls` list won't contain `find_volume`/`snapshot` entries (assertion mismatch).

- [x] **Step 3: Add the hook to `update()`**

In `deploy/aws/src/glow_deploy/core.py`, inside `update()`, between the `wait_for_runner_bootstrap_completion(...)` call and `prepare_runner_repository(...)`:

```python
    wait_for_ssm_online(instance_id, config.aws_region, config.session)
    wait_for_runner_bootstrap_completion(
        instance_id, config.aws_region, config.session
    )

    volume_id = find_root_volume_id(instance_id, config.aws_region, config.session)
    create_snapshot(
        volume_id, config.domain_name, "pre-update", config.aws_region, config.session
    )

    prepare_runner_repository(
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "test_update" -v`
Expected: PASS (both the pre-existing `test_update_prepares_repository_before_rerunning_userdata` and the new test).

- [x] **Step 5: Commit**

```bash
git add deploy/aws/src/glow_deploy/core.py deploy/aws/tests/test_core.py
git commit -m "feat: snapshot the root volume before update() mutates the instance"
```

---

### Task 4: Snapshot and delete the volume after `destroy()` tears down infrastructure

**Files:**
- Modify: `deploy/aws/src/glow_deploy/core.py:1144-1202` (`destroy()`)
- Test: `deploy/aws/tests/test_core.py`

**Interfaces:**
- Consumes: `find_root_volume_id`, `create_snapshot` (Task 2), `read_terraform_outputs` (`core.py:374`).

`destroy()` currently never looks up `instance_id` — Terraform state (which is where `runner_instance_id` comes from) disappears once `terraform destroy` completes, so the volume ID must be captured *before* that call. `delete_on_termination=false` already means the volume survives termination unaffected by this change; this task adds a snapshot of it plus an explicit `delete_volume` call afterward, replacing today's "leaked forever" behavior.

- [x] **Step 1: Write the failing test**

Add to `deploy/aws/tests/test_core.py`, after the snapshot-primitive tests added in Task 2 (or anywhere before `_make_config`, e.g. right before `def _make_config`):

```python
def test_destroy_captures_volume_before_terraform_destroy_then_snapshots_and_deletes_it(
    monkeypatch,
):
    calls: list[tuple] = []

    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core, "terraform_init", lambda bucket, region, session=None: None
    )
    monkeypatch.setattr(core.binaries, "terraform_binary", lambda: "terraform")

    def fake_run_command(args, check=True, cwd=None, env=None):
        if "output" in args:
            return SimpleNamespace(
                stdout='{"runner_instance_id": {"value": "i-1234567890"}}'
            )
        calls.append(("run_command", args))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(core, "run_command", fake_run_command)
    monkeypatch.setattr(
        core,
        "find_root_volume_id",
        lambda instance_id, region, session=None: calls.append(("find_volume", instance_id))
        or "vol-abc123",
    )
    monkeypatch.setattr(
        core,
        "create_snapshot",
        lambda volume_id, domain, reason, region, session=None: calls.append(
            ("snapshot", volume_id, domain, reason)
        )
        or "snap-1234567890",
    )
    fake_ec2 = _FakeEc2Client()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    core.destroy(_make_config(dry_run=False))

    run_command_calls = [c for c in calls if c[0] == "run_command"]
    assert len(run_command_calls) == 1
    assert "destroy" in run_command_calls[0][1]

    assert calls == [
        ("find_volume", "i-1234567890"),
        ("run_command", run_command_calls[0][1]),
        ("snapshot", "vol-abc123", "example.com", "pre-destroy"),
    ]
    assert fake_ec2.delete_volume_calls == [{"VolumeId": "vol-abc123"}]


def test_destroy_dry_run_does_not_snapshot_or_delete_volume(monkeypatch):
    calls: list[tuple] = []

    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core, "terraform_init", lambda bucket, region, session=None: None
    )
    monkeypatch.setattr(core.binaries, "terraform_binary", lambda: "terraform")

    def fake_run_command(args, check=True, cwd=None, env=None):
        if "output" in args:
            return SimpleNamespace(
                stdout='{"runner_instance_id": {"value": "i-1234567890"}}'
            )
        return SimpleNamespace(stdout="plan output")

    monkeypatch.setattr(core, "run_command", fake_run_command)
    monkeypatch.setattr(
        core,
        "find_root_volume_id",
        lambda instance_id, region, session=None: "vol-abc123",
    )
    monkeypatch.setattr(
        core,
        "create_snapshot",
        lambda *args, **kwargs: calls.append(("snapshot",)) or "snap-1234567890",
    )
    fake_ec2 = _FakeEc2Client()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)

    core.destroy(_make_config(dry_run=True))

    assert calls == []
    assert fake_ec2.delete_volume_calls == []
```

`_FakeEc2Client` (defined at `test_core.py:184`) needs a `delete_volume` method for this test — extend it in the same edit:

```python
class _FakeEc2Client:
    def __init__(self, response=None):
        self._response = response
        self.describe_instances_calls: list[dict] = []
        self.create_tags_calls: list[dict] = []
        self.delete_volume_calls: list[dict] = []

    def describe_instances(self, **kwargs):
        self.describe_instances_calls.append(kwargs)
        return self._response

    def create_tags(self, **kwargs):
        self.create_tags_calls.append(kwargs)

    def delete_volume(self, **kwargs):
        self.delete_volume_calls.append(kwargs)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k test_destroy -v`
Expected: FAIL — `calls` is missing `find_volume`/`snapshot` entries, and `AttributeError: 'SimpleNamespace' object has no attribute` is not raised, but the assertions on `calls`/`delete_volume_calls` fail because `destroy()` doesn't do any of this yet.

- [x] **Step 3: Implement the hook in `destroy()`**

Replace the body of `destroy()` in `deploy/aws/src/glow_deploy/core.py` (lines 1156-1202) with:

```python
    write_line(f"[deploy] Destroying {config.domain_name}")

    bucket = ensure_state_bucket(
        config.aws_region, config.domain_name, config.session
    )
    terraform_init(bucket, config.aws_region, config.session)

    env = _subprocess_env(config.session)

    outputs = read_terraform_outputs(env=env)
    instance_id = outputs["runner_instance_id"]
    volume_id = find_root_volume_id(instance_id, config.aws_region, config.session)

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

    create_snapshot(
        volume_id, config.domain_name, "pre-destroy", config.aws_region, config.session
    )
    ec2 = _client(config.session, "ec2", config.aws_region)
    ec2.delete_volume(VolumeId=volume_id)
    write_line(f"[deploy] Deleted volume {volume_id}")

    write_line(f"[deploy] {config.domain_name} destroyed")
```

(Only change to the docstring: none needed, it already documents the placeholder-values rationale which still holds.)

- [x] **Step 4: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "test_destroy or test_update" -v`
Expected: PASS (4 tests: the 2 new destroy tests plus the 2 update tests, confirming no regression).

- [x] **Step 5: Run the full core test suite**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -v`
Expected: PASS, all tests.

- [x] **Step 6: Commit**

```bash
git add deploy/aws/src/glow_deploy/core.py deploy/aws/tests/test_core.py
git commit -m "feat: snapshot and delete the root volume after destroy()"
```

---

### Task 5: Data-only restore into a new deployment

**Files:**
- Modify: `deploy/aws/src/glow_deploy/core.py:57-69` (`Config`), and after `list_snapshots`/`delete_snapshot` (Task 2) for the new function; `provision()` at `core.py:1014-1081`
- Test: `deploy/aws/tests/test_core.py`

**Interfaces:**
- Produces: `restore_snapshot_data(instance_id: str, snapshot_id: str, region: str, session: boto3.Session | None = None) -> None`; `Config.restore_from_snapshot_id: str = ""`.
- Consumes: `_client`, `write_line`, `wait_with_spinner`, `run_ssm_command` (`core.py:593`).

Mechanism: create a volume from the snapshot in the target instance's AZ, attach it as a secondary device, run an SSM script that stops the compose stack, rsyncs `glow-postgres`/`odk-postgres` out of the mounted volume into `/var/lib/glow/{glow,odk}-postgres` (the real data dirs — see `deploy/aws/runtime/activate-stack.sh`'s `STATE_DIR=/var/lib/glow`), restarts the stack, then detaches and deletes the temporary volume. The attached device is located via the AWS-documented `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_<volume-id-without-dashes>` symlink, since Nitro instances remap the requested device name.

- [x] **Step 1: Write the failing tests**

Add to `deploy/aws/tests/test_core.py`, near the other snapshot tests (after the `test_delete_snapshot_calls_ec2` test added in Task 2):

```python
class _FakeEc2ClientForRestore:
    def __init__(self):
        self.create_volume_calls: list[dict] = []
        self.attach_volume_calls: list[dict] = []
        self.detach_volume_calls: list[dict] = []
        self.delete_volume_calls: list[dict] = []
        self._volume_state = "available"

    def describe_instances(self, **kwargs):
        return {
            "Reservations": [
                {"Instances": [{"Placement": {"AvailabilityZone": "eu-west-2a"}}]}
            ]
        }

    def create_volume(self, **kwargs):
        self.create_volume_calls.append(kwargs)
        return {"VolumeId": "vol-restore123"}

    def describe_volumes(self, **kwargs):
        return {"Volumes": [{"State": self._volume_state}]}

    def attach_volume(self, **kwargs):
        self.attach_volume_calls.append(kwargs)
        self._volume_state = "in-use"

    def detach_volume(self, **kwargs):
        self.detach_volume_calls.append(kwargs)
        self._volume_state = "available"

    def delete_volume(self, **kwargs):
        self.delete_volume_calls.append(kwargs)


def test_restore_snapshot_data_creates_attaches_and_cleans_up_volume(monkeypatch):
    fake_ec2 = _FakeEc2ClientForRestore()
    monkeypatch.setattr(core, "_client", lambda session, service, region: fake_ec2)
    ssm_calls = []
    monkeypatch.setattr(
        core,
        "run_ssm_command",
        lambda instance_id, region, commands, comment, timeout=1800, session=None, on_tick=None: ssm_calls.append(
            (instance_id, commands, comment)
        ),
    )

    core.restore_snapshot_data("i-1234567890", "snap-1234567890", "eu-west-2", session=None)

    assert fake_ec2.create_volume_calls == [
        {"SnapshotId": "snap-1234567890", "AvailabilityZone": "eu-west-2a"}
    ]
    assert fake_ec2.attach_volume_calls == [
        {"VolumeId": "vol-restore123", "InstanceId": "i-1234567890", "Device": "/dev/sdf"}
    ]
    assert len(ssm_calls) == 1
    assert ssm_calls[0][0] == "i-1234567890"
    assert "volrestore123" in ssm_calls[0][1][0]
    assert "glow-postgres" in ssm_calls[0][1][0]
    assert "odk-postgres" in ssm_calls[0][1][0]
    assert fake_ec2.detach_volume_calls == [
        {"VolumeId": "vol-restore123", "InstanceId": "i-1234567890"}
    ]
    assert fake_ec2.delete_volume_calls == [{"VolumeId": "vol-restore123"}]


def test_provision_restores_snapshot_data_when_requested(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core, "find_ami_in_account", lambda region, commit, session=None: "ami-12345678"
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
    for name in (
        "wait_for_ssm_online",
        "wait_for_runner_bootstrap_completion",
        "verify_runner_health",
    ):
        monkeypatch.setattr(core, name, lambda instance_id, region, session=None: None)
    monkeypatch.setattr(
        core,
        "prepare_runner_repository",
        lambda instance_id, region, repo_url, checkout_ref, session=None: None,
    )
    monkeypatch.setattr(
        core,
        "rerun_runner_userdata",
        lambda instance_id, region, domain_name, env=None, session=None: None,
    )
    monkeypatch.setattr(
        core,
        "restore_snapshot_data",
        lambda instance_id, snapshot_id, region, session=None: calls.append(
            (instance_id, snapshot_id)
        ),
    )

    core.provision(_make_config(restore_from_snapshot_id="snap-1234567890"))

    assert calls == [("i-1234567890", "snap-1234567890")]


def test_provision_skips_restore_when_no_snapshot_requested(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        core, "ensure_state_bucket", lambda region, domain, session=None: "bucket"
    )
    monkeypatch.setattr(
        core, "find_ami_in_account", lambda region, commit, session=None: "ami-12345678"
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
    for name in (
        "wait_for_ssm_online",
        "wait_for_runner_bootstrap_completion",
        "verify_runner_health",
    ):
        monkeypatch.setattr(core, name, lambda instance_id, region, session=None: None)
    monkeypatch.setattr(
        core,
        "prepare_runner_repository",
        lambda instance_id, region, repo_url, checkout_ref, session=None: None,
    )
    monkeypatch.setattr(
        core,
        "rerun_runner_userdata",
        lambda instance_id, region, domain_name, env=None, session=None: None,
    )
    monkeypatch.setattr(
        core,
        "restore_snapshot_data",
        lambda *args, **kwargs: calls.append("called"),
    )

    core.provision(_make_config())

    assert calls == []
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "restore_snapshot_data or restores_snapshot or skips_restore" -v`
Expected: FAIL — `restore_snapshot_data` doesn't exist yet, and `_make_config(restore_from_snapshot_id=...)` fails with `TypeError: __init__() got an unexpected keyword argument`.

- [x] **Step 3: Add the `Config` field**

In `deploy/aws/src/glow_deploy/core.py`, in the `Config` dataclass (`core.py:57-69`), add the new field after `certificate_arn`:

```python
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
    restore_from_snapshot_id: str = ""
    session: boto3.Session | None = None
```

- [x] **Step 4: Implement `restore_snapshot_data`**

Add after `delete_snapshot` (added in Task 2) in `deploy/aws/src/glow_deploy/core.py`:

```python
_RESTORE_SNAPSHOT_DATA_SCRIPT = """set -euo pipefail
by_id_name=$(ls /dev/disk/by-id/ | grep {volume_suffix} | head -n1)
device="/dev/disk/by-id/${{by_id_name}}"
mount_point=/mnt/glow-restore
mkdir -p "${{mount_point}}"

partition="${{device}}-part1"
if [[ -e "${{partition}}" ]]; then
  mount "${{partition}}" "${{mount_point}}"
else
  mount "${{device}}" "${{mount_point}}"
fi

cd /opt/glow
docker compose --profile odk --env-file /var/lib/glow/.deploy/share/.env.runtime -f compose.yml down

rsync -a --delete "${{mount_point}}/glow-postgres/" /var/lib/glow/glow-postgres/
rsync -a --delete "${{mount_point}}/odk-postgres/" /var/lib/glow/odk-postgres/

docker compose --profile odk --env-file /var/lib/glow/.deploy/share/.env.runtime -f compose.yml up -d

umount "${{mount_point}}"
"""


def restore_snapshot_data(
    instance_id: str,
    snapshot_id: str,
    region: str,
    session: boto3.Session | None = None,
) -> None:
    """Copy glow-postgres/odk-postgres out of a snapshot into a running
    instance's live data directories — data-only restore, not a whole-volume
    swap (see "Restore semantics" in the spec for why).
    """
    ec2 = _client(session, "ec2", region)
    instance = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    az = instance["Placement"]["AvailabilityZone"]

    write_line(f"[deploy] Creating restore volume from snapshot {snapshot_id}")
    volume_id = ec2.create_volume(SnapshotId=snapshot_id, AvailabilityZone=az)["VolumeId"]

    def volume_available() -> bool:
        return ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]["State"] == "available"

    wait_with_spinner(f"Waiting for volume {volume_id}", volume_available, timeout=300)

    device = "/dev/sdf"
    ec2.attach_volume(VolumeId=volume_id, InstanceId=instance_id, Device=device)

    def volume_attached() -> bool:
        return ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]["State"] == "in-use"

    wait_with_spinner(f"Attaching volume {volume_id}", volume_attached, timeout=300)

    try:
        script = _RESTORE_SNAPSHOT_DATA_SCRIPT.format(volume_suffix=volume_id.replace("-", ""))
        run_ssm_command(
            instance_id,
            region,
            [script],
            "restore Postgres data from snapshot",
            timeout=900,
            session=session,
        )
    finally:
        write_line(f"[deploy] Detaching and deleting restore volume {volume_id}")
        ec2.detach_volume(VolumeId=volume_id, InstanceId=instance_id)
        wait_with_spinner(f"Detaching volume {volume_id}", volume_available, timeout=300)
        ec2.delete_volume(VolumeId=volume_id)

    write_line("[deploy] Restore complete")
```

- [x] **Step 5: Hook the conditional restore step into `provision()`**

In `deploy/aws/src/glow_deploy/core.py`, inside `provision()`, right after `verify_runner_health(instance_id, config.aws_region, config.session)` (currently line 1073) and before the final `write_line("[deploy] Deployment complete!")`:

```python
    verify_runner_health(instance_id, config.aws_region, config.session)

    if config.restore_from_snapshot_id:
        restore_snapshot_data(
            instance_id, config.restore_from_snapshot_id, config.aws_region, config.session
        )

    write_line("[deploy] Deployment complete!")
```

- [x] **Step 6: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -k "restore_snapshot_data or restores_snapshot or skips_restore" -v`
Expected: PASS (3 tests).

- [x] **Step 7: Run the full core test suite**

Run: `cd deploy/aws && python -m pytest tests/test_core.py -v`
Expected: PASS, all tests.

- [x] **Step 8: Commit**

```bash
git add deploy/aws/src/glow_deploy/core.py deploy/aws/tests/test_core.py
git commit -m "feat: add data-only snapshot restore into provisioned deployments"
```

---

### Task 6: Snapshots card on the deployment detail page

**Files:**
- Modify: `deploy/aws/src/glow_deploy/gui/routes/deployments.py:189-203` (`deployment_detail`)
- Modify: `deploy/aws/src/glow_deploy/gui/templates/deployment_detail.html`
- Test: `deploy/aws/tests/gui/test_routes.py`

**Interfaces:**
- Consumes: `core.list_snapshots`, `core.delete_snapshot` (Task 2).
- Produces: new route `POST /deployments/{domain}/snapshots/{snapshot_id}/delete`; `deployment_detail.html`'s context gains a `snapshots` key.

- [x] **Step 1: Write the failing tests**

Add to `deploy/aws/tests/gui/test_routes.py`, after `test_deployment_detail_404s_for_unknown_domain` (after line 411, before `test_update_plan_then_apply_updates`):

```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "lists_snapshots_for_the_domain or delete_snapshot_route" -v`
Expected: FAIL — `test_deployment_detail_lists_snapshots_for_the_domain` fails because `snapshots` isn't in the template context (`AttributeError`/`jinja2.exceptions.UndefinedError` surfaced as a 500, or the assertion on response text fails); `test_deployment_detail_delete_snapshot_route` fails with 404 (no such route).

- [x] **Step 3: Add the route changes**

In `deploy/aws/src/glow_deploy/gui/routes/deployments.py`, change the `deployment_detail` route (currently lines 189-203) to fetch and pass snapshots — rename the unused `_session` param to `session` since it's now needed:

```python
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
```

Add a new route immediately after `deployment_detail`:

```python
@router.post("/deployments/{domain}/snapshots/{snapshot_id}/delete", response_class=HTMLResponse)
def delete_deployment_snapshot(
    request: Request, domain: str, snapshot_id: str, session=Depends(require_session)
):
    core.delete_snapshot(snapshot_id, request.app.state.region, session)
    return RedirectResponse(f"/deployments/{domain}", status_code=303)
```

- [x] **Step 4: Add the Snapshots card to the template**

In `deploy/aws/src/glow_deploy/gui/templates/deployment_detail.html`, add after the `</details>` that closes the Update card (the last line before `{% endblock %}`):

```html
  </details>

  <details>
    <summary>Snapshots</summary>
    <div class="card">
      {% if not snapshots %}
      <p class="text-muted">No snapshots for this deployment yet.</p>
      {% else %}
      <ul class="list-plain">
        {% for snap in snapshots %}
        <li>
          <span>{{ snap.started_at }} — {{ snap.reason }} ({{ snap.size_gb }} GB, {{ snap.state }})</span>
          <form method="post" action="/deployments/{{ deployment.domain }}/snapshots/{{ snap.snapshot_id }}/delete"
                onsubmit="return confirm('Delete this snapshot? This cannot be undone.')">
            <button type="submit" class="button button-danger-ghost">Delete</button>
          </form>
        </li>
        {% endfor %}
      </ul>
      {% endif %}
    </div>
  </details>
{% endblock %}
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "lists_snapshots_for_the_domain or delete_snapshot_route" -v`
Expected: PASS (2 tests).

- [x] **Step 6: Run the full GUI route test suite**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -v`
Expected: PASS, all tests (confirms the `_session` → `session` rename didn't break anything else).

- [x] **Step 7: Commit**

```bash
git add deploy/aws/src/glow_deploy/gui/routes/deployments.py deploy/aws/src/glow_deploy/gui/templates/deployment_detail.html deploy/aws/tests/gui/test_routes.py
git commit -m "feat: show and manage a deployment's snapshots on its detail page"
```

---

### Task 7: Global `/snapshots` page

**Files:**
- Create: `deploy/aws/src/glow_deploy/gui/templates/snapshots.html`
- Modify: `deploy/aws/src/glow_deploy/gui/routes/deployments.py` (add two routes)
- Modify: `deploy/aws/src/glow_deploy/gui/templates/base.html` (nav link)
- Test: `deploy/aws/tests/gui/test_routes.py`

**Interfaces:**
- Consumes: `core.list_snapshots`, `core.delete_snapshot` (Task 2).
- Produces: `GET /snapshots`, `POST /snapshots/{snapshot_id}/delete`.

A separate global delete route (rather than reusing the per-domain one from Task 6) is needed because an orphaned snapshot's domain no longer has a live deployment page to redirect back to — this route redirects to `/snapshots` instead of `/deployments/{domain}`.

- [x] **Step 1: Write the failing tests**

Add to `deploy/aws/tests/gui/test_routes.py`, after the two tests added in Task 6:

```python
def test_snapshots_page_lists_all_snapshots_including_orphans(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core,
        "list_snapshots",
        lambda region, session: [
            {
                "snapshot_id": "snap-1",
                "domain": "example.com",
                "reason": "pre-update",
                "started_at": "2026-01-01T00:00:00Z",
                "size_gb": 100,
                "state": "completed",
            },
            {
                "snapshot_id": "snap-2",
                "domain": "gone.example.com",
                "reason": "pre-destroy",
                "started_at": "2026-01-02T00:00:00Z",
                "size_gb": 80,
                "state": "completed",
            },
        ],
    )

    response = client.get("/snapshots")

    assert response.status_code == 200
    assert "snap-1" in response.text
    assert "snap-2" in response.text
    assert "gone.example.com" in response.text


def test_snapshots_page_delete_route_redirects_to_snapshots_list(client, monkeypatch):
    _sign_in(client)
    delete_calls = []
    monkeypatch.setattr(
        core,
        "delete_snapshot",
        lambda snapshot_id, region, session: delete_calls.append(snapshot_id),
    )

    response = client.post("/snapshots/snap-2/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/snapshots"
    assert delete_calls == ["snap-2"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "snapshots_page" -v`
Expected: FAIL with 404 (neither route exists).

- [x] **Step 3: Add the routes**

In `deploy/aws/src/glow_deploy/gui/routes/deployments.py`, add at the end of the file:

```python
@router.get("/snapshots", response_class=HTMLResponse)
def all_snapshots(request: Request, session=Depends(require_session)):
    snapshots = core.list_snapshots(request.app.state.region, session)
    return templates.TemplateResponse(request, "snapshots.html", {"snapshots": snapshots})


@router.post("/snapshots/{snapshot_id}/delete", response_class=HTMLResponse)
def delete_global_snapshot(request: Request, snapshot_id: str, session=Depends(require_session)):
    core.delete_snapshot(snapshot_id, request.app.state.region, session)
    return RedirectResponse("/snapshots", status_code=303)
```

- [x] **Step 4: Create the template**

Create `deploy/aws/src/glow_deploy/gui/templates/snapshots.html`:

```html
{% extends "base.html" %}
{% block title %}Snapshots — Glow Deploy{% endblock %}
{% block content %}
  <h1>Snapshots</h1>
  {% if not snapshots %}
  <p>No snapshots found in this account/region.</p>
  {% else %}
  <ul class="list-plain">
    {% for snap in snapshots %}
    <li class="card">
      <div class="deployment-row">
        <strong>{{ snap.domain or "(unknown domain)" }}</strong>
        <span class="badge">{{ snap.reason }} — {{ snap.state }}</span>
      </div>
      <dl>
        <dt>Snapshot ID</dt><dd>{{ snap.snapshot_id }}</dd>
        <dt>Created</dt><dd>{{ snap.started_at }}</dd>
        <dt>Size</dt><dd>{{ snap.size_gb }} GB</dd>
      </dl>
      <p class="links">
        <form method="post" action="/snapshots/{{ snap.snapshot_id }}/delete"
              onsubmit="return confirm('Delete this snapshot? This cannot be undone.')">
          <button type="submit" class="button button-danger-ghost">Delete</button>
        </form>
      </p>
    </li>
    {% endfor %}
  </ul>
  {% endif %}
{% endblock %}
```

- [x] **Step 5: Link it from the nav**

In `deploy/aws/src/glow_deploy/gui/templates/base.html`, add a nav link in the `topbar`, before the sign-out form:

```html
  <header class="topbar">
    <a class="brand" href="/deployments">Glow Deploy</a>
    {% if request.app.state.session %}
    <a href="/snapshots" class="button button-ghost">Snapshots</a>
    <form method="post" action="/signin/out">
      <button type="submit" class="button button-ghost">Sign out</button>
    </form>
    {% endif %}
  </header>
```

- [x] **Step 6: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "snapshots_page" -v`
Expected: PASS (2 tests).

- [x] **Step 7: Run the full GUI route test suite**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -v`
Expected: PASS, all tests.

- [x] **Step 8: Commit**

```bash
git add deploy/aws/src/glow_deploy/gui/routes/deployments.py deploy/aws/src/glow_deploy/gui/templates/snapshots.html deploy/aws/src/glow_deploy/gui/templates/base.html deploy/aws/tests/gui/test_routes.py
git commit -m "feat: add a global /snapshots page listing snapshots across all domains"
```

---

### Task 8: Restore-from-snapshot picker on the new-deployment form

**Files:**
- Modify: `deploy/aws/src/glow_deploy/gui/routes/deployments.py` (`new_deployment_form`, `new_deployment_plan`, `new_deployment_apply`)
- Modify: `deploy/aws/src/glow_deploy/gui/templates/new_deployment.html`
- Test: `deploy/aws/tests/gui/test_routes.py`

**Interfaces:**
- Consumes: `core.list_snapshots` (Task 2), `Config.restore_from_snapshot_id` (Task 5).
- No new route or job kind — `restore_from_snapshot_id` flows through the existing `config_fields` dict, so `job_progress.html`'s existing generic hidden-field loop (`{% for key, value in job.meta.config.items() %}`) already replays it from the plan job to the apply form with no template change needed there.

- [x] **Step 1: Write the failing test**

Add to `deploy/aws/tests/gui/test_routes.py`, near the other new-deployment tests (search the file for `"/deployments/new/plan"` to find that section and add nearby):

```python
def test_new_deployment_form_lists_snapshots_for_restore_picker(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(
        core,
        "list_snapshots",
        lambda region, session: [
            {
                "snapshot_id": "snap-1",
                "domain": "example.com",
                "reason": "pre-destroy",
                "started_at": "2026-01-01T00:00:00Z",
                "size_gb": 100,
                "state": "completed",
            }
        ],
    )

    response = client.get("/deployments/new")

    assert response.status_code == 200
    assert "snap-1" in response.text


def test_new_deployment_plan_then_apply_threads_restore_snapshot_id(client, monkeypatch):
    _sign_in(client)
    monkeypatch.setattr(core, "list_snapshots", lambda region, session: [])
    monkeypatch.setattr(
        github_api, "resolve_git_commit_via_github", lambda repo_url, ref: "d" * 40
    )
    monkeypatch.setattr(
        core,
        "provision",
        lambda config: {"runner_instance_id": "i-123", "alb_dns_name": "alb.example.com"},
    )

    plan_response = client.post(
        "/deployments/new/plan",
        data={
            "domain": "example.com",
            "git_ref": "v2",
            "aws_region": "eu-west-2",
            "restore_from_snapshot_id": "snap-1",
        },
        follow_redirects=False,
    )
    assert plan_response.status_code == 303
    plan_job_id = plan_response.headers["location"].removeprefix("/jobs/")
    _wait_for_job(client, plan_job_id)

    apply_page = client.get(f"/jobs/{plan_job_id}")
    assert 'name="restore_from_snapshot_id" value="snap-1"' in apply_page.text

    apply_calls = []
    monkeypatch.setattr(
        core, "provision", lambda config: apply_calls.append(config) or None
    )
    apply_response = client.post(
        "/deployments/new/apply",
        data={
            "domain_name": "example.com",
            "git_repo_url": "https://github.com/OxWRC/glow.git",
            "git_ref": "v2",
            "git_commit": "d" * 40,
            "aws_region": "eu-west-2",
            "app_name": "glow-core",
            "runner_instance_type": "t3.medium",
            "runner_root_volume_size_gb": "100",
            "restore_from_snapshot_id": "snap-1",
        },
        follow_redirects=False,
    )
    assert apply_response.status_code == 303
    apply_job_id = apply_response.headers["location"].removeprefix("/jobs/")
    _wait_for_job(client, apply_job_id)

    assert apply_calls[0].restore_from_snapshot_id == "snap-1"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "restore_snapshot_id or lists_snapshots_for_restore" -v`
Expected: FAIL — `list_snapshots` isn't called by `new_deployment_form` (template has no `snapshots` var, so `"snap-1" in response.text` fails), and `restore_from_snapshot_id` isn't a recognized form field so it's silently dropped (the hidden-input assertion and `apply_calls[0].restore_from_snapshot_id` assertion fail — `Config` already has the field from Task 5, so this fails on missing wiring, not a missing dataclass field).

- [x] **Step 3: Wire up the routes**

In `deploy/aws/src/glow_deploy/gui/routes/deployments.py`:

Change `new_deployment_form` (rename `_session` to `session` and add snapshots to context):

```python
@router.get("/deployments/new", response_class=HTMLResponse)
def new_deployment_form(request: Request, session=Depends(require_session)):
    return templates.TemplateResponse(request, "new_deployment.html", {"error": None,
            "available_versions": _sorted_available_versions(request),
            "snapshots": core.list_snapshots(request.app.state.region, session),
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
```

Change `new_deployment_plan` to accept and thread the field — add the `Form(...)` parameter and include it in `config_fields`, and also add `"snapshots": core.list_snapshots(request.app.state.region, session)` to the error-branch re-render (the `except DeployError` block) since that branch re-renders the same template:

```python
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
                "snapshots": core.list_snapshots(request.app.state.region, session),
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
```

Change `new_deployment_apply` to accept and pass the field through:

```python
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
```

- [x] **Step 4: Add the dropdown to the template**

In `deploy/aws/src/glow_deploy/gui/templates/new_deployment.html`, add before the final `<button type="submit" class="button">Start deployment</button>`:

```html
    <label>Restore data from snapshot (optional)
      <select name="restore_from_snapshot_id">
        <option value="">— none, start fresh —</option>
        {% for snap in snapshots %}
        <option value="{{ snap.snapshot_id }}">{{ snap.domain }} — {{ snap.reason }} — {{ snap.started_at }} ({{ snap.snapshot_id }})</option>
        {% endfor %}
      </select>
    </label>
    <button type="submit" class="button">Start deployment</button>
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd deploy/aws && python -m pytest tests/gui/test_routes.py -k "restore_snapshot_id or lists_snapshots_for_restore" -v`
Expected: PASS (2 tests).

- [x] **Step 6: Run the full test suite (core + GUI)**

Run: `cd deploy/aws && python -m pytest tests/ -v`
Expected: PASS, all tests, no regressions.

- [x] **Step 7: Commit**

```bash
git add deploy/aws/src/glow_deploy/gui/routes/deployments.py deploy/aws/src/glow_deploy/gui/templates/new_deployment.html deploy/aws/tests/gui/test_routes.py
git commit -m "feat: add snapshot restore picker to the new-deployment form"
```
