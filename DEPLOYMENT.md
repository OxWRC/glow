# Glow Deployment Quick Start

## Prerequisites

### Option 1: Docker Compose (Easiest)

1. Docker with Compose plugin
2. AWS credentials (via SSO, profile, or environment variables)

### Option 2: Docker (Recommended)

1. Docker
2. AWS credentials (via SSO, profile, or environment variables)

### Option 3: Direct Python

1. `uv`
2. `terraform`
3. `packer`
4. AWS credentials for EC2, ALB, ACM, S3, IAM, SSM, and CloudWatch
5. Network access to `api.github.com` (used to resolve git tags/branches to commits — no local `git` needed)

## Initial Provision

### Using Docker Compose with AWS SSO (Easiest for Individual Users)

```bash
# Authenticate on your host machine first
aws sso login --profile my-profile

# Navigate to deploy directory and run with SSO profile
cd deploy/aws
AWS_PROFILE=my-profile docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123
```

### Using Docker Compose with Environment Credentials (CI/CD)

```bash
cd deploy/aws
docker compose --profile env run --rm deploy-env \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123
```

### Using Docker Run with AWS SSO

```bash
# Authenticate on your host machine first
aws sso login --profile my-profile

# Build and run deployment
docker build -t glow-launcher -f deploy/aws/Dockerfile .
docker run --rm -it \
  -e AWS_PROFILE=my-profile \
  -e AWS_REGION=eu-west-2 \
  -v "$HOME/.aws:/aws-host:ro" \
  glow-launcher \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123
```

### Using Docker Run with Environment Credentials

```bash
docker build -t glow-launcher -f deploy/aws/Dockerfile .
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  -e AWS_REGION=eu-west-2 \
  glow-launcher \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123
```

### Using Direct Python

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:eu-west-2:123456789012:certificate/abc123
```

## Update Existing Deployment

### Using Docker Compose with AWS SSO

```bash
cd deploy/aws
AWS_PROFILE=my-profile docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

### Using Docker Compose with Environment Credentials

```bash
cd deploy/aws
docker compose --profile env run --rm deploy-env \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

### Using Docker Run with AWS SSO

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

### Using Docker Run with Environment Credentials

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

### Using Direct Python

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

## What It Does

For Docker-based AWS SSO runs, the launcher mounts the host `~/.aws` directory read-only and copies it into a container-local `~/.aws` before invoking the deploy script. Any cache or token refresh written by the AWS CLI inside Docker stays inside the container and does not leave root-owned files on the host.

### Initial Provision

1. Resolves the git reference to a commit SHA
2. Checks if a runner AMI exists in your AWS account for that commit
3. Builds the AMI using Packer if not found
4. Applies Terraform to create:
   - ALB with HTTPS listeners
   - Security groups and IAM roles
   - Single long-lived EC2 instance with 100GB root volume
   - CloudWatch log groups
5. Waits for the instance to bootstrap and activate the stack
6. Verifies service health

### Update

1. Finds the existing instance via Terraform state
2. Sends an SSM command to:
   - Fetch and checkout the target git reference
   - Run optional `deploy/update-instance.sh` hook
   - Stop and restart containers
3. Verifies service health

## Flags

- `--domain` (required): deployment domain
- `--certificate-arn`: ACM certificate ARN
- `--git-ref`: git tag/branch/commit (default: main)
- `--aws-region`: AWS region (default: eu-west-2 or AWS_REGION env var)
- `--runner-instance-type`: instance type (default: t3.medium)
- `--runner-root-volume-size-gb`: root volume size (default: 100GB)
- `--force-rebuild-ami`: rebuild AMI even if one exists
- `--dry-run`: plan only, do not apply
- `--update`: update existing instance instead of provision
- `--demo`: provision a demo deployment (see below); fixed at provision time

## Demo Deployments

`--demo` provisions an instance that serves fictional data with no Cognito. Anyone can log in from the role picker as an admin or a school user. Demo mode is fixed at provision time: `--update` keeps it, and re-provisioning the same domain with a different mode fails.

On every activation, `deploy/aws/runtime/activate-stack.sh` does the following:

- Adds `compose.demo.yml`. ODK Postgres is built from the pre-seeded `dev` target, and `GLOW_DEMO_MODE` / `PUBLIC_DEMO_MODE` are set.
- Writes the Cognito values blank.
- Resets the seeded ODK users (`admin@glow.local`, `api@glow.local`) from the public `devpassword` to the generated passwords in `.env.admin` / `.env.runtime`.
- Wipes and re-seeds Glow's users and schools (`glow-api demo reset`).

The seeded ODK data lives inside the `postgres14` container, not on the persistent volume. Recreating that container restores the seed, including `devpassword`, until the next activation rotates it again. Re-run `--update` after any manual container recreation.

The seeded `devpassword` also works from the moment the stack starts until activation rotates it, a window of a few minutes. If activation fails in that window, it stays live. Re-running `--update` closes it.

Demo deployments need the seed dump from Git LFS. Runner AMIs install `git-lfs`, and the repository checkout pulls LFS objects. On an older AMI without `git-lfs`, demo activation stops with an error instead of building from the pointer file. Snapshot restore is refused for demo deployments.

## Certificate Assumption

Deployment requires an already-issued ACM certificate for:

- `<domain>`
- `api.<domain>`
- `odk.<domain>`

Provide the ARN with `--certificate-arn`.

## DNS Ownership

DNS is assumed to be managed externally. After deployment, configure these records with your DNS provider:

- `<domain>` → ALB DNS (CNAME or ALIAS)
- `api.<domain>` → ALB DNS (CNAME)
- `odk.<domain>` → ALB DNS (CNAME)

The deploy script prints the exact ALB DNS name.

## Architecture

- **AMI**: Thin image with Docker, git, and dependencies pre-installed
- **Instance**: Long-lived EC2 with persistent root volume
- **State**: All data stored in `/var/lib/glow` on the root volume
- **Updates**: In-place via SSM, no instance replacement
- **Backups**: Use AWS Backup or EBS snapshots

## Notes

- The instance is protected with `delete_on_termination = false` on the root volume
- Terraform ignores AMI and userdata changes to prevent accidental replacement
- For dependency updates, rebuild the AMI with `--force-rebuild-ami`
- Routine application updates use `--update` and happen over SSM
