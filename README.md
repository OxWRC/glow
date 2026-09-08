# glow-core

API and Dashboard for GLOW longitudinal questionnaire data.

## Overview

This project has two components:

- **API** (`/api`) — a read-only FastAPI service that provides suppression-safe access to student questionnaire data
- **Dashboard** (`/dashboard`) — a SvelteKit app for authenticated users to view and query data via interactive charts

In local compose, the Glow services are exposed directly:
- Dashboard → `http://localhost:3000`
- API → `http://localhost:8000`

The self-hosted ODK Central stack is available behind the optional `odk` compose profile.
Its public entrypoint defaults to `http://localhost:8080` and `https://localhost:8443`.

## Quick Start

### Option 1: Devcontainer (Recommended)

For the most consistent development experience:

1. Install [Docker](https://www.docker.com/products/docker-desktop) and [VS Code](https://code.visualstudio.com/) with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Open this repo in VS Code
3. Click "Reopen in Container" when prompted (or use Command Palette → "Dev Containers: Reopen in Container")
4. Wait for the devcontainer to build and app services to start
5. Generate and seed test data (see [Data Collection & Format](#data-collection--format) section below)
6. Create users:
   ```bash
   docker compose exec api glow-api users create --admin admin
   ```

The dashboard will be available at <http://localhost:3000> and the API at <http://localhost:8000>.

The devcontainer provides:
- Python 3.12 + Node 22 pre-installed
- `uv` for fast Python package management
- Playwright with browser dependencies
- All app services running through the production-like compose base plus dev overrides

### Option 2: Docker Compose (Host-Based)

`compose.yml` now defines the production-like core stack: Glow dashboard, Glow API, Glow Postgres, and an optional ODK Central profile. Local development layers on `compose.override.yml`, which switches the API and dashboard into reload/watch mode and bind-mounts the workspace.

```bash
# Set a strong JWT secret
export GLOW_SECRET_KEY="your-strong-secret-here"

# Set a Postgres password for the local stack
export POSTGRES_PASSWORD="your-local-postgres-password"

# Start the full stack including ODK Central
docker compose --profile odk up --build

# In a separate terminal: Generate and seed test data
# (see "Data Collection & Format" section below for detailed steps)

# Create the first admin user (in a separate terminal)
docker compose exec api glow-api users create --admin admin
```

The dashboard will be available at <http://localhost:3000>.

### ODK Central

The optional `odk` profile adds a self-hosted ODK Central stack using the official Central service/nginx images plus the supporting Postgres, Redis, Enketo, SMTP, and secrets services they require.

Network boundaries in the compose setup are:

- ODK Central internal services talk only on the `odk_internal` network.
- Glow Dashboard can only reach Glow API on `dashboard_api`.
- Glow API can only reach its own Postgres on `api_db`.
- The only shared route between ODK Central and Glow is `service` ↔ `api` on `odk_api`.

This keeps ODK Central isolated from the dashboard while still allowing API-mediated data flow.

## API

### Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `GLOW_ODK_API_URL` | `http://localhost:8383` | ODK Central API base URL |
| `GLOW_ODK_API_EMAIL` | `test@example.com` | ODK Central admin email |
| `GLOW_ODK_API_PASSWORD` | `test-password` | ODK Central admin password |
| `GLOW_ODK_PROJECT_ID` | `1` | ODK Central project ID |
| `GLOW_ODK_FORM_ID` | `bewell_questionnaire` | ODK form ID |
| `GLOW_DATA_CACHE_PATH` | *(none)* | Optional path for DataFrame cache (e.g., `/data/cache.parquet`) |
| `GLOW_DATA_REFRESH_HOURS` | `1` | How often to poll ODK Central for new data |
| `GLOW_MIN_N` | `5` | Minimum distinct student count for suppression |
| `GLOW_SECRET_KEY` | *(insecure default)* | JWT signing secret — **must be set in production** |
| `GLOW_ALGORITHM` | `HS256` | JWT algorithm |
| `GLOW_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Token lifetime (8 hours) |
| `GLOW_METADATA_DATABASE_URL` | `sqlite:///./metadata.db` | SQLAlchemy metadata database URL |
| `GLOW_CORS_ORIGINS` | `["*"]` | JSON list of allowed CORS origins |

### Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Health check |
| `POST` | `/auth/login` | — | Login (returns JWT) |
| `GET` | `/schools` | User | List schools with query options |
| `POST` | `/query` | User | Execute query with blanket suppression |
| `GET` | `/admin/users` | Admin | List users |
| `POST` | `/admin/users` | Admin | Create user |
| `PUT` | `/admin/users/{id}` | Admin | Update user |
| `DELETE` | `/admin/users/{id}` | Admin | Delete user |
| `GET` | `/admin/me` | User | Current user info |

Interactive documentation is available at `/docs` (Swagger UI) and `/redoc`.

### Admin CLI

```bash
# Initialise the database
glow-api db init

# List users
glow-api users list

# Create a regular user
glow-api users create alice

# Create an admin user
glow-api users create --admin bob

# Update a user
glow-api users update alice --scope '{"filters": {"school": ["Greenwood"]}}'

# Delete a user
glow-api users delete alice
```

### Suppression

N is counted as **distinct students (uid)**, not rows. 
Any materialized result cell where the contributing student count is less than `GLOW_MIN_N` is suppressed
(set to empty in the CSV output) and recorded in the `suppressions` field of the response.

## Dashboard

The SvelteKit dashboard provides:

- **Login** — JWT-based authentication
- **Home** — pre-built overview charts
- **Query Builder** — a step-based analytical query builder with built-in suppression
- **Admin** — user CRUD (admin users only)

## Development

### Configuration Files

This repo uses a centralized approach to minimize drift between dev/test/prod:

- **`compose.yml`** — production-like core stack (dashboard, API, Postgres)
  - Used by: CI smoke tests and as the base for local development
  - Defines runtime topology and network boundaries
  - Includes the optional `odk` profile for self-hosted ODK Central
- **`compose.override.yml`** — local development overrides
  - Enables source bind mounts, Vite/Uvicorn reload, and dev-friendly defaults
  - Applied automatically by `docker compose up`
- **`compose.test.yml`** — test-specific overrides
  - Deterministic secrets, tighter healthchecks, no restart policies
  - Usage: `docker compose -f compose.yml -f compose.test.yml up`
- **`.devcontainer/`** — VS Code devcontainer configuration
  - Provides tooling shell (Python 3.12, Node 22, uv, Playwright)
  - Delegates app services to `compose.yml` (no duplication)
  - Mounts Docker socket for "Docker outside of Docker"
- **`.env.example`** — documented environment variable defaults

### Devcontainer (Recommended)

The devcontainer provides a fully configured development environment. After opening in the devcontainer:

**API development:**
```bash
cd api
uv pip install -e ".[test]"
uv run pytest
```

**Dashboard development:**
```bash
cd dashboard
npm install
npm run check    # Type checking
npm run lint     # Linting
```

**Running smoke tests:**
```bash
bash scripts/smoke_compose.sh
```

### Manual Setup (without devcontainer)

**API:**
```bash
cd api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
glow-api db init
uvicorn glow_api.main:app --reload
pytest
```

**Dashboard:**
```bash
cd dashboard
npm install
npm run dev
npm run check
```

## Deployment (AWS)

See `deploy/aws/` for the AWS deployment package.

See `DEPLOYMENT.md` for the quick start guide.

The deployment uses:
- Local Packer builds for thin runner AMIs
- Single-pass Terraform for infrastructure
- Long-lived EC2 instance with persistent root volume
- SSM-based in-place updates
- Application Load Balancer with host-based routing
- ACM for TLS certificates with external DNS validation
- Auto-configured ODK Central integration

### Initial Provision

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --certificate-arn arn:aws:acm:...
```

### Update

```bash
uv run --project deploy/aws glow-deploy \
  --domain eu.glow-project.org \
  --git-ref v1.2.3 \
  --update
```

Useful flags:

- `--certificate-arn arn:aws:acm:...`
- `--git-ref v1.2.3`
- `--aws-region eu-west-2`
- `--force-rebuild-ami`
- `--dry-run`
- `--update`

DNS is assumed to be managed externally. The deploy script prints the ALB DNS records to configure with your DNS provider.

## Data Collection & Format

### Production Data Flow

In production, data is collected via [ODK Central](https://docs.getodk.org/central-intro/):

```
Real users → ODK Collect app → ODK Central → Glow API → Dashboard
```

The current BeWell questionnaire form is included in this repo at `odk-forms/bewell_questionnaire_v2.xml`. The historical v1 form, PHQ-9 form, and demographics form also live under `odk-forms/`.

- `uid` — student identifier (used for N counting in suppression)
- `school` — school identifier carried on each questionnaire form
- Questionnaire items from the [#BeeWell GM Survey](https://beewellprogramme.org) (e.g. `bw_wbeing_1`–`bw_wbeing_7` for SWEMWBS wellbeing, `bw_emodies_1`–`bw_emodies_10` for emotional difficulties, and ~120 further items)

The form is automatically uploaded to ODK Central during deployment by `deploy/aws/runtime/activate-stack.sh`.

### Development/Testing Data Flow

For local development and testing, synthetic data is generated and seeded into ODK Central:

```
glow-dummies → glow_base.csv → transform_mock_data.py → mock_seed/*.csv + manifest.csv → seed_odk_test_data.py → rewrite_odk_submission_timestamps.py → ODK Central → Glow API → Dashboard
```

#### Step 1: Generate Synthetic Data

Use [glow-dummies](https://github.com/OxfordRSE/glow-dummies) (a Julia CLI, requires Julia ≥ 1.10) to generate realistic base test data. Clone it as a sibling checkout and run it locally — its `examples/glow_model.toml` config is not published anywhere, so point `--config` at the local file:

```bash
# Install glow-dummies (sibling checkout to this repo)
git clone https://github.com/OxfordRSE/glow-dummies.git ../glow-dummies
cd ../glow-dummies && julia --project=. -e 'import Pkg; Pkg.instantiate()' && cd -

# Generate canonical base data
julia ../glow-dummies/bin/glow_dummies \
  --config ../glow-dummies/examples/glow_model.toml \
  --seed 42 \
  --output csv \
  > data/glow_base_raw.csv

# Slim mock data to ~12k submissions (from ~60k) to reduce seeding time
python scripts/odk/slim_mock_data.py \
  --input data/glow_base_raw.csv --output data/glow_base.csv
```

The first command generates ~60k raw submissions; the slimming step reduces this to ~12k (preserving test diversity per school) to cut ODK seeding time from ~5-6 hours down to ~15-30 minutes. Both outputs are clean wide CSVs with BeeWell v2, demographics, PHQ-9, and a synthetic overlap-control item.

### Step 2: Transform Base Data Into Per-Form Seed CSVs

```bash
python scripts/odk/transform_mock_data.py \
  --input data/glow_base.csv \
  --output-dir data/mock_seed \
  --forms-dir odk-forms
```

This produces deterministic per-form CSVs and a manifest describing school wave patterns, BeeWell version usage, missingness, and intended submission timestamps.

#### Step 3: Seed ODK Central

Upload the generated data to your local ODK Central instance:

```bash
uv run scripts/odk/seed_odk_test_data.py \
  --seed-dir data/mock_seed \
  --manifest data/mock_seed/manifest.csv \
  --forms-dir odk-forms \
  --odk-url https://localhost:8443 \
  --email admin@example.com \
  --password your-odk-password \
  --project-id 1

# Or limit each phase for faster testing
uv run scripts/odk/seed_odk_test_data.py \
  --seed-dir data/mock_seed \
  --manifest data/mock_seed/manifest.csv \
  --forms-dir odk-forms \
  --odk-url https://localhost:8443 \
  --email admin@example.com \
  --password your-odk-password \
  --project-id 1 \
  --limit 100
```

#### Step 4: Rewrite Submission Timestamps

```bash
python scripts/odk/rewrite_odk_submission_timestamps.py \
  --manifest data/mock_seed/manifest.csv
```

The seeding workflow is idempotent at the submission level and uses the
generated `instance_id` values from the transformed per-form CSVs.

#### Step 5: Verify Data Ingestion

The Glow API polls ODK Central hourly (configurable via `GLOW_DATA_REFRESH_HOURS`). To force an immediate refresh during development:

```bash
# Restart the API container to trigger a fresh data load
docker compose restart api

# Check the API logs
docker compose logs -f api
```

You should see logs showing data being fetched from ODK Central and cached.

### Minimal Demo Data

For quick smoke testing, the checked-in demo dataset still exists, but the main
development/demo workflow now uses the canonical base-data plus transform flow
described above.
