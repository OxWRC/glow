# GLOW: Core

This project is a reproducible data collection, storage, and dashboarding application.
It is designed for deployment in multiple discrete regions.
Development rules for each subcomponent are listed below.

## API `./api`

The API ingests data and stores it as a .sqlite3 file.
It then exposes that data to the Dashboard for display.

It is **very important** that individual people's data are not retrievable from the system,
including by advanced techniques such as differencing attacks.
To help guard against this we have an any->all rule:
if ANY cell in a filter/aggregate would end up with too small an N (zeros are okay),
then ALL cells are suppressed.
For example, if we group a class by sex (M: 14, F: 13) that's fine,
and if we group them by ethnicity (A: 5, B: 7, C: 15), that's also fine,
but if we group by both the whole lot are suppressed: (MA: 5, MB: **2**, MC: 7, FA: 0, FB: 5, FC: 8)
because there aren't enough in the MB cell.

### Tech stack:
- `Python 3.12`, `uv`, `pyproject.toml`
- `SQLite` (may use Postgres for deployment, but remain SQLite compatible)
- `FastAPI`

### Test Driven Development

We use test driven development.
When changes are to be made, tests are adjusted or added to cover those changes first and observed to fail.
The changes are then made and the tests are shown to pass.

### Clean as you go

Unused imports should be removed in files you touch.
If you orphan a function by removing its last call site you should remove the function.

### SemVer

We use semantic versioning.
The bump rules are different for beta (Major version 0) and release (Major version 1+).

While we are in beta, we **do not need to retain backwards-compatibility**.

#### Beta (Major version 0)
- Breaking changes  -> bump minor; otherwise
- New API surface -> bump patch

#### Release (Major versions 1+)
- Breaking changes -> new major version
- New API surface -> bump minor
- Otherwise: new logic -> bump patch

### Definition of Done:
- If new logic was added, tests are included to cover it
- All tests pass
- All code is Ruff formatted

## Dashboard `./dashboard`

The Dashboard exposes API data in graphic and tabular form to users.

### Tech stack:
- SvelteKit (Svelte 5)
- TypeScript
- Vite
- Storybook

### SemVer

Same rules as for API.

### A11y
- All HTML is written using appropriate semantic tags
- Native HTML tags (e.g. <details>, <dialog>) are preferred over home-rolled or package-supplied variants
- All tests use accessible selectors, never class-based or id-based ones.
- Colour contrasts use theming and are appropriate
- We do not use colour gradients
- Animations are gated behind reduced-motion preferences

### i18n
- All text strings pass through an i18n function to resolve them into localised variants
- All links take the form /[locale]/... which defaults to 'en' and is preserved across internal links

### Tests
- All tests use accessible selectors, never class-based or id-based ones
- All stories include an interaction test
- All interaction tests are different and cover the distinguishing features of the story

### Definition of Done:
- The Storybook MSW handlers exactly match the relevant API return surfaces
- If new components were added, they include Storybook stories with tests
- If new logic was added, Storybook tests are included to demonstrate it
- Tests pass (`npm run test`)
- `npx tsc` is clean
- `npm run build` succeeds

## Terraform `./terraform`

Not yet of concern.

## Admin `./admin`

The Admin view is a management and audit platform that allows access to audit logging and user/data management.
It **does not allow read access to data**. Admins are thus _not_ data viewers.

Not yet of concern.

## Development

We use docker compose to keep stuff working in development environments.
Always ensure that the development environment exposes the backend and frontend appropriately.

Front/backend containers are restarted frequently during development.
Restarting either should not restart the other.

## Deployment

### Release Tagging

Two independent tag namespaces, do not conflate:
- `v*.*.*` (e.g. `v0.1.4`) — the docker compose stack (api, dashboard, ODK). This is what `--git-ref` in the AWS deploy GUI checks out onto the EC2 instance.
- `gui-v*.*.*` (e.g. `gui-v0.0.2`) — the deploy tool itself (`deploy/aws/`), packaged and released via `.github/workflows/gui-release.yml`, which only triggers on `gui-v*` tags. A `v*.*.*` tag never builds a GUI binary.

### AWS Deployment (Recommended)

For production deployments on AWS, see `deploy/aws/README.md` and `DEPLOYMENT.md`.

The AWS deployment provides:
- Automated infrastructure provisioning with Terraform
- Local Packer AMI builds in your AWS account
- Long-lived EC2 instance with persistent root volume
- SSM-based in-place updates for routine releases
- HTTPS via AWS Certificate Manager with external DNS validation
- Application Load Balancer with health checks
- CloudWatch logging

#### Using Docker Compose (Easiest)

Initial provision with AWS SSO:
```bash
aws sso login --profile my-profile
cd deploy/aws
AWS_PROFILE=my-profile docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

Update existing deployment:
```bash
cd deploy/aws
AWS_PROFILE=my-profile docker compose --profile sso run --rm deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

For CI/CD with environment credentials:
```bash
cd deploy/aws
docker compose --profile env run --rm deploy-env \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

#### Using Docker Run

Build the launcher image:
```bash
docker build -t glow-launcher -f deploy/aws/Dockerfile .
```

Initial provision with AWS SSO:
```bash
aws sso login --profile my-profile

docker run --rm -it \
  -e AWS_PROFILE=my-profile \
  -e AWS_REGION=eu-west-2 \
  -v "$HOME/.aws:/aws-host:ro" \
  glow-launcher \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

Update existing deployment:
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

For CI/CD, use environment credentials instead:
```bash
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  -e AWS_REGION=eu-west-2 \
  glow-launcher \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

#### Using Direct Python

Quick start:
```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

Update existing deployment:
```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

Notes:
- DNS is assumed to be managed externally
- Requires an already-issued ACM certificate for root, `api.`, and `odk.` hostnames
- The deploy script prints ALB DNS records to configure with your DNS provider
- Instance updates happen in-place via SSM without replacement
- All persistent data lives on the instance root volume

### Self-Hosted Deployment (Manual)

The repository no longer provides a first-class non-AWS deployment orchestrator.

For a manual VM/server deployment, use the checked-in `compose.yml` stack, persistent storage for `docker-mount-data/`, and your own reverse proxy or load balancer.

#### Prerequisites
- Ubuntu 22.04+ or Debian 11+ server
- Root or sudo access
- Domain name pointing to your server
- Ports 80/443 open for web traffic

#### Deployment Steps

1. **Clone the repository:**
```bash
git clone https://github.com/OxWRC/glow.git
cd glow
git checkout v1.2.3  # Use latest release tag
```

2. **Prepare persistent storage:**
```bash
mkdir -p docker-mount-data
```

3. **Install Docker Engine and Docker Compose plugin.**

4. **Create the runtime environment and start the stack manually.**

This repository no longer ships a generic host activation script for that workflow, so any manual deployment should be treated as an operator-managed compose installation rather than a supported automated path.

5. **Access services:**
- Dashboard: `http://glow.example.com` 
- API: `http://glow.example.com:8000`
- ODK Central: `http://glow.example.com:8080`

6. **Set up reverse proxy for HTTPS:**

You'll need to configure a reverse proxy (nginx, Apache, Caddy, etc.) to:
- Terminate HTTPS with your SSL certificate
- Forward requests to the appropriate ports

Example nginx configuration:
```nginx
server {
    listen 80;
    server_name glow.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl;
    server_name api.glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl;
    server_name odk.glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

#### Data Persistence

All persistent data is stored in `./docker-mount-data/`:
- `glow-postgres/` - Glow API database
- `odk-postgres/` - ODK Central database
- `odk-secrets/` - ODK encryption keys
- `.deploy/.env.admin` - Admin credentials
- Other service data

**Backup this directory regularly!**

Recommended backup strategy:
```bash
# Stop services
cd /path/to/glow
sudo docker compose --profile odk stop

# Backup data
sudo tar czf glow-backup-$(date +%Y%m%d).tar.gz docker-mount-data/

# Restart services
sudo docker compose --profile odk start
```

#### Updates

To update to a new version:

```bash
cd /path/to/glow
git fetch --tags
git checkout v1.3.0  # New version
docker compose --profile odk up -d --build
```

This manual workflow preserves data in `docker-mount-data/`, but version compatibility checks are your responsibility outside the AWS deploy tool.

#### Retrieving Credentials

Admin credentials are stored in:
```bash
cat ./docker-mount-data/.deploy/.env.admin
```

This file contains:
- ODK Central admin email
- ODK Central admin password

#### Troubleshooting

**Services not starting:**
```bash
sudo docker compose ps
sudo docker compose logs
```

**Check versions:**
```bash
cat docker-mount-data/.glow-deployment-version
```

**Major version upgrade blocked:**
If you see an error about major version upgrades, consult `UPGRADING.md` for migration instructions.

**Data location:**
If you want data on a separate partition, you can:
1. Mount your partition at `/data`
2. Create symlink: `ln -s /data ./docker-mount-data`
3. Run activation script as normal
