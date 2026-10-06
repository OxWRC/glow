# Glow AWS Deployment

Simplified deployment using:

- Local Packer builds for thin runner AMIs
- Single-pass Terraform for infrastructure
- SSM-based in-place updates for routine releases
- Long-lived EC2 instance with persistent root volume

## GUI (recommended)

Most deployments and updates don't need any of the CLI/Docker steps below —
download the packaged GUI instead of installing Python, Terraform, Packer, or
Docker.

1. Download the build for your OS from the
   [Releases page](https://github.com/OxWRC/glow/releases) — Windows
   (`.zip`), macOS (`.tar.gz`), or Linux (`.AppImage` or `.tar.gz`).
2. Run it. It opens a browser tab at `http://127.0.0.1:<port>` — no terminal
   needed.
3. Sign in with AWS SSO (recommended) or manual access keys.
4. Use "New deployment" to provision, or open an existing deployment to
   update it. Both flows show a plan/review step before anything actually
   runs.

Builds are unsigned for now, so the OS will warn about an unidentified
developer/publisher on first run — click through it (Windows: "More info" →
"Run anyway"; macOS: right-click the app → "Open").

### Running the GUI from source

For developing the GUI, or trying an unreleased version. Needs `uv`, Node.js,
and `terraform` + `packer` on `PATH` (or set `GLOW_DEPLOY_TERRAFORM_BIN` /
`GLOW_DEPLOY_PACKER_BIN`). From the repo root:

```bash
# Compile the frontend TypeScript into gui/static/js/ (gitignored; re-run after TS changes)
(cd deploy/aws/src/glow_deploy/gui/frontend && npm ci && npm run build)

# Start the GUI; it opens a browser tab at http://127.0.0.1:<random port>
uv run --project deploy/aws glow-deploy-gui
```

From source the version shows as `dev` and the update check is skipped. The
server exits about 10 seconds after the last tab closes (unless a job is
running); launching again while one is running reopens the existing tab.

Everything below this section covers the underlying CLI and raw Terraform,
for advanced use or automation (CI, scripting) where the GUI doesn't fit.

## Prerequisites

### Option 1: Docker (Recommended)

1. Docker
2. AWS credentials (via SSO, profile, or environment variables)

### Option 2: Direct Python

1. `uv`
2. `terraform`
3. `packer`
4. AWS credentials for the target account
5. Network access to `api.github.com` (used to resolve git tags/branches to commits — no local `git` needed)

## Usage

### Docker Compose (Easiest)

The simplest way to deploy is using Docker Compose with profiles. Use `--profile sso` for AWS SSO authentication or
`--profile env` for environment credentials.

#### Initial Provision

If this AWS account hosts a public Route 53 hosted zone for the domain (or a parent of it), the ACM certificate and
DNS records are created and validated automatically — no extra flags needed. Deploying on a domain hosted elsewhere
(the common case when hosting on someone else's domain) requires an existing ACM certificate for it, passed via
`--certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123`.

**Using AWS SSO (for individual users):**

```bash
# First, authenticate on your host machine
export AWS_PROFILE=my-profile
aws sso login

# Navigate to deploy/aws directory
cd deploy/aws

# Run deployment with SSO profile
docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org
```

**Using environment credentials (for CI or temporary credentials):**

```bash
cd deploy/aws

# Run deployment with env profile
docker compose --profile env run --rm deploy-env \
  --domain eu.glow-project.org
```

Note: Ensure `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optionally `AWS_SESSION_TOKEN` are set in your
environment.

#### Subsequent Updates

**Using AWS SSO:**

```bash
AWS_PROFILE=my-profile 
aws sso login
cd deploy/aws

docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

**Using environment credentials:**

```bash
cd deploy/aws

docker compose --profile env run --rm deploy-env \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

### Docker Run (Alternative)

If you prefer not to use Docker Compose, you can use `docker run` directly.

#### Build the launcher image

From the repository root:

```bash
docker build -t glow-launcher -f deploy/aws/Dockerfile .
```

#### Initial Provision

**Using AWS SSO (for individual users):**

```bash
# First, authenticate on your host machine
aws sso login --profile my-profile

# Then run the deployment
docker run --rm -it \
  -e AWS_PROFILE=my-profile \
  -e AWS_REGION=eu-west-2 \
  -v "$HOME/.aws:/aws-host:ro" \
  glow-launcher \
  --domain eu.glow-project.org
```

**Using environment credentials (for CI or temporary credentials):**

```bash
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  -e AWS_REGION=eu-west-2 \
  glow-launcher \
  --domain eu.glow-project.org
```

This will:

1. Build a runner AMI in your AWS account using Packer
2. Apply Terraform to create infrastructure and a single EC2 instance
3. Wait for the instance to bootstrap and activate the stack
4. Verify health checks

#### Subsequent Updates

**Using AWS SSO:**

```bash
docker run --rm -it \
  -e AWS_PROFILE=my-profile \
  -e AWS_REGION=eu-west-2 \
  -v "$HOME/.aws:/aws-host:ro" \
  glow-launcher \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

**Using environment credentials:**

```bash
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  -e AWS_REGION=eu-west-2 \
  glow-launcher \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

This will:

1. Find the existing instance via Terraform outputs
2. Send an SSM command to update the repository and restart containers
3. Verify health checks

### Direct Python Deployment

If you prefer to install dependencies locally:

#### Initial Provision

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org
```

#### Subsequent Updates

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

### Command-Line Flags

- `--domain` (required): deployment domain
- `--certificate-arn`: existing ACM certificate ARN, required only when this account has no Route 53 hosted zone for the domain (or a parent of it)
- `--git-ref`: git tag/branch/commit to deploy (default: main)
- `--aws-region`: AWS region (default: eu-west-2 or AWS_REGION env var)
- `--runner-instance-type`: EC2 instance type (default: t3.medium)
- `--runner-root-volume-size-gb`: root volume size in GB (default: 100)
- `--force-rebuild-ami`: force AMI rebuild even if one exists
- `--demo`: provision a demo deployment (fictional data, no login; the GUI has a matching "Demo deployment" checkbox). The mode is fixed at provision: re-provisioning an existing deployment with a different mode is refused, so destroy it and provision again to change it
- `--dry-run`: plan only, do not apply
- `--update`: update existing instance instead of provision

### AWS Authentication

The deploy script uses standard AWS credential resolution via boto3:

1. **Environment variables**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
2. **AWS config and credentials files**: `~/.aws/config` and `~/.aws/credentials` (respects `AWS_PROFILE`)
3. **IAM role** (when running in EC2, ECS, Lambda, etc.)

For containerized deployments:

- **AWS SSO users**: Mount `~/.aws` read-only and authenticate on the host before running the container
- The launcher copies `~/.aws` into container-local `~/.aws` at startup, so cache or token refreshes inside Docker do not write root-owned files back to the host
- **CI/CD**: Pass credentials as environment variables
- **Credential process or vault tools**: If using `credential_process`, `aws-vault`, or OS keychain helpers, either:
  - Include the helper binary in the image, or
  - Use environment credentials instead

### Advanced: driving Terraform directly

Both the GUI and the `glow-deploy` CLI are wrappers around a single Terraform
root module at `deploy/aws/terraform/`. You can skip both and drive it
yourself.

**You'll need:**

- A runner AMI already built and tagged (`Component=glow-runner`,
  `GitCommit=<sha>`) — either run `packer build` in `deploy/aws/runner/`
  yourself, or find an existing one in the account:
  `aws ec2 describe-images --owners self --filters Name=tag:Component,Values=glow-runner`.
- An S3 state bucket. `glow-deploy` creates one automatically, named
  `<domain-with-dots-replaced-by-dashes>-glow-deploy-state-<account-id>`;
  reuse that name (with versioning and public-access-block enabled), or bring
  your own.

**Init** (backend config is required — there's no default backend block):

```bash
cd deploy/aws/terraform
terraform init \
  -backend-config=bucket=<your-state-bucket> \
  -backend-config=key=main.tfstate \
  -backend-config=region=<aws-region>
```

**Variables** (all required except `runner_root_volume_size_gb`, which
defaults to `100`, and `certificate_arn`/`hosted_zone_id`, of which exactly
one should be set):

| Variable | Meaning |
| --- | --- |
| `app_name` | Deployment name tag (matches CLI's `--app-name`, default `glow-core`) |
| `aws_region` | AWS region |
| `hosted_zone_id` | Route 53 hosted zone ID to auto-manage the certificate and DNS in; leave unset if using `certificate_arn` |
| `certificate_arn` | Existing ACM certificate ARN for the ALB listener; leave unset if using `hosted_zone_id` |
| `domain_name` | Deployment domain |
| `git_repo_url` | Git repository to check out on the runner |
| `git_ref` | Branch/tag to record (informational — the actual checkout uses `git_checkout_ref`) |
| `git_checkout_ref` | Commit SHA to check out |
| `runner_ami_id` | AMI ID built above |
| `runner_instance_type` | EC2 instance type |
| `runner_root_volume_size_gb` | Root volume size in GB |

```bash
terraform plan  -var-file=my.tfvars.json
terraform apply -var-file=my.tfvars.json
```

Applying does **not** run the runner's post-boot repository checkout/activation
— that's a separate SSM step the CLI/GUI run afterwards
(`prepare_runner_repository` + `rerun_runner_userdata` in
`glow_deploy/core.py`). After a raw `terraform apply`, either replicate that
manually via SSM, or run `glow-deploy --update` against the domain to finish
activation.

## Architecture

### AMI

The runner AMI is thin:

- Base: Amazon Linux 2023
- Pre-installed: Docker, git, curl, jq, CloudWatch agent
- Build artifact is tagged with git commit SHA

### Instance

The EC2 instance is long-lived:

- Managed by Terraform with `lifecycle.ignore_changes = [ami, user_data]`
- Root EBS volume stores all persistent state under `/var/lib/glow`
- `delete_on_termination = false` protects data

### Persistent State

All application state lives in `/var/lib/glow`:

- Postgres data
- ODK secrets
- Runtime configuration
- Deployment metadata

The repository checkout at `/opt/glow` has `docker-mount-data` symlinked to `/var/lib/glow`.

### Updates

Routine updates happen via SSM without replacing the instance:

1. Clone or refresh `/opt/glow` to the target repository
2. Checkout the target git ref
3. Rerun `runner-userdata.sh.tpl` with git overrides
4. Reconcile containers via `activate-stack.sh`
5. Verify health

AMI rebuilds are only needed for:

- First deployment into an account
- Dependency changes in `install-runner-deps.sh`
- Explicit instance replacement

## Snapshots

The tool snapshots the runner's root volume at these lifecycle events:

| `Reason` tag | When | Waits for completion? |
|---|---|---|
| `post-create` | end of a successful provision (baseline, so later snapshots are incremental; a failure only warns) | no |
| `pre-update` | before each `--update` | no |
| `pre-destroy` | before the volume is deleted on destroy | yes (up to 30 min; on timeout the volume is kept and the snapshot continues in AWS) |

`scheduled` and `manual` are reserved `Reason` values with no producer yet.

Each snapshot is tagged to link it to its deployment:

- `Name`: `glow-<domain>-<reason>-<UTC timestamp>`
- `Domain`, `Component=glow-runner-snapshot`, `Reason`
- `InstanceId`: the source runner instance, which differs between successive deployments of the same domain
- `GitRef` and `GitCommit`: the app version whose data is on disk
- `DemoMode`
- `GlowGUIVersion`: the tool version that took the snapshot

Snapshots taken before these tags existed carry only `Domain`, `Component` and `Reason`.

## Notes

- If this account has a public Route 53 hosted zone for the domain (or a parent of it), the ACM certificate and the dashboard/api/odk DNS records are created and validated automatically; otherwise pass `--certificate-arn` and manage DNS externally
- Backups via AWS Backup or EBS snapshots are recommended
