#!/usr/bin/env bash
# dev-init.sh - Initialize GLOW development environment
#
# This script sets up a complete local development environment with:
# - ODK Central with test data
# - Glow API with admin user
# - Dashboard ready to use
#
# Usage:
#   ./dev-init.sh              # Initialize environment (all test data)
#   ./dev-init.sh --limit 100  # Initialize with limited test data
#   ./dev-init.sh --reset      # Wipe everything and start fresh
#   ./dev-init.sh --help       # Show this help

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RESET=false
LIMIT=""
SKIP_SEED=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RESET_COLOR='\033[0m'

# ============================================================================
# Helper Functions
# ============================================================================

info() {
  echo -e "${GREEN}✓${RESET_COLOR} $*"
}

warn() {
  echo -e "${YELLOW}⚠${RESET_COLOR} $*"
}

error() {
  echo -e "${RED}✗${RESET_COLOR} $*" >&2
}

step() {
  echo ""
  echo -e "${BLUE}==>${RESET_COLOR} $*"
}

show_help() {
  cat <<EOF
GLOW Development Environment Initialization

Usage:
  ./dev-init.sh [OPTIONS]

Options:
  --limit N     Seed only N rows of test data (default: all ~12k rows)
  --reset       Wipe all data volumes and start fresh
  --help        Show this help message

Examples:
  # Initialize with all test data
  ./dev-init.sh

  # Initialize with only 100 rows for faster setup
  ./dev-init.sh --limit 100

  # Reset everything and reinitialize
  ./dev-init.sh --reset

After initialization:
  - Use 'docker compose up' for subsequent starts
  - Credentials are saved in .env.dev (gitignored)
  - Visit http://localhost:3000 to access the dashboard

EOF
}

check_command() {
  local cmd="$1"
  local install_hint="$2"
  
  if ! command -v "$cmd" &>/dev/null; then
    error "Required command '$cmd' not found"
    echo "   Install: $install_hint"
    exit 1
  fi
}

generate_password() {
  head -c1024 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c24
}

wait_for_odk() {
  local max_wait=180  # 3 minutes
  local waited=0
  
  echo -n "⏳ Waiting for ODK Central to be ready"
  
  while [[ $waited -lt $max_wait ]]; do
    # Check if nginx is responding (even with 421/301 is fine - means it's up)
    if curl -sf -H "Host: odk.local" http://localhost:8080/ >/dev/null 2>&1 || \
       curl -s -H "Host: odk.local" http://localhost:8080/ 2>&1 | grep -q "30[0-9]\|421"; then
      echo " ✅"
      return 0
    fi
    echo -n "."
    sleep 5
    waited=$((waited + 5))
  done
  
  echo " ❌"
  error "ODK Central failed to start within ${max_wait}s"
  echo "   Check logs: docker compose logs odk-service"
  return 1
}

wait_for_api() {
  local max_wait=60  # 1 minute
  local waited=0
  
  echo -n "⏳ Waiting for Glow API to be ready"
  
  while [[ $waited -lt $max_wait ]]; do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
      echo " ✅"
      return 0
    fi
    echo -n "."
    sleep 3
    waited=$((waited + 3))
  done
  
  echo " ❌"
  error "Glow API failed to start within ${max_wait}s"
  echo "   Check logs: docker compose logs api"
  return 1
}

# ============================================================================
# Main Script
# ============================================================================

# Step 1: Parse Arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --reset)
      RESET=true
      shift
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --help)
      show_help
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      show_help
      exit 1
      ;;
  esac
done

# Print header
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  GLOW Development Environment Initialization"
echo "════════════════════════════════════════════════════════════════"

# Step 2: Check Prerequisites
step "Checking prerequisites"

check_command "docker" "https://docs.docker.com/get-docker/"
check_command "jq" "apt install jq (or brew install jq)"
check_command "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh"

# Check docker compose (supports both 'docker compose' and 'docker-compose')
if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
  error "Docker Compose not found"
  echo "   Install: https://docs.docker.com/compose/install/"
  exit 1
fi

info "All prerequisites satisfied"

# Step 3: Handle Reset Flag
if [[ $RESET == true ]]; then
  step "Resetting environment (--reset flag)"
  
  warn "This will delete all data volumes and containers!"
  
  docker compose down -v 2>/dev/null || true
  
  # Use docker to remove the mount directories (they may be owned by root)
  if [[ -d docker-mount-data ]]; then
    warn "Removing docker-mount-data directory..."
    # Try regular rm first
    if ! rm -rf docker-mount-data/ 2>/dev/null; then
      # If that fails, show instructions for manual cleanup
      error "Could not remove docker-mount-data (permission denied)"
      echo "   Run: sudo rm -rf docker-mount-data/"
      echo "   Then re-run this script"
      exit 1
    fi
  fi
  
  rm -f .env.dev
  
  info "Reset complete - starting fresh initialization"
fi

# Step 4: Generate or Reuse Credentials  
step "Generating credentials"

if [[ -f .env.dev ]]; then
  info "Found existing .env.dev - reusing credentials (idempotent)"
  source .env.dev
else
  info "No existing .env.dev - generating new credentials"
  
  ODK_ADMIN_EMAIL="admin@glow.local"
  ODK_ADMIN_PASSWORD=$(generate_password)
  ODK_API_EMAIL="api@glow.local"
  ODK_API_PASSWORD=$(generate_password)
  GLOW_ADMIN_USER="admin"
  GLOW_ADMIN_PASSWORD="admin"
  
  # Save to .env.dev
  cat > .env.dev <<EOF
# Auto-generated by dev-init.sh - DO NOT COMMIT
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# ODK Central Admin
ODK_ADMIN_EMAIL=${ODK_ADMIN_EMAIL}
ODK_ADMIN_PASSWORD=${ODK_ADMIN_PASSWORD}

# ODK Central API User (for Glow API)
ODK_API_EMAIL=${ODK_API_EMAIL}
ODK_API_PASSWORD=${ODK_API_PASSWORD}

# Glow API Admin
GLOW_ADMIN_USER=${GLOW_ADMIN_USER}
GLOW_ADMIN_PASSWORD=${GLOW_ADMIN_PASSWORD}
EOF
fi

info "Credentials ready: ${ODK_ADMIN_EMAIL}"

# Update .env with credentials needed by docker-compose
# (Do this early so they're available when containers start)
# Remove old ODK section if it exists and add updated credentials
if grep -q "# ODK Central Integration" .env 2>/dev/null; then
  # Remove old ODK section (from marker to next blank line or EOF)
  sed -i '/# ODK Central Integration/,/^$/d' .env
fi

cat >> .env <<EOF

# ODK Central Integration (added by dev-init.sh)
# Use HTTPS through nginx (Basic Auth requires HTTPS)
GLOW_ODK_API_URL=https://nginx
GLOW_ODK_API_EMAIL=${ODK_API_EMAIL}
GLOW_ODK_API_PASSWORD=${ODK_API_PASSWORD}
GLOW_ODK_PROJECT_ID=1
GLOW_ODK_FORM_ID=bewell_questionnaire
GLOW_ODK_VERIFY_SSL=false
EOF
info "Updated ODK credentials in .env for docker-compose"

# Step 6: Start ODK Services
step "Starting ODK Central stack (this may take 2-3 minutes)"
info "Dependencies: postgres14, mail, secrets, pyxform, enketo, redis..."

# Start ODK and its dependencies
docker compose up -d --build odk-service nginx pyxform

wait_for_odk

# Step 7: Create ODK Admin User & Project
step "Configuring ODK Central"

source .env.dev

info "Creating ODK admin user: ${ODK_ADMIN_EMAIL}"
CREATE_OUTPUT=$(echo "${ODK_ADMIN_PASSWORD}" | docker compose exec -T odk-service \
  node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-create 2>&1 || true)

if echo "$CREATE_OUTPUT" | grep -qi "already exists"; then
  info "(User already exists - updating password to match .env.dev)"
  echo "${ODK_ADMIN_PASSWORD}" | docker compose exec -T odk-service \
    node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-set-password >/dev/null 2>&1 || {
    warn "Failed to update password (user may not exist yet)"
  }
elif echo "$CREATE_OUTPUT" | grep -qiE '(success|"email".*"id")'; then
  # Success if output contains "success" or JSON with email and id fields
  info "Admin user created successfully"
elif echo "$CREATE_OUTPUT" | grep -qiE '(error|failed)'; then
  warn "Failed to create admin user: $CREATE_OUTPUT"
else
  warn "Unexpected output from user-create: $CREATE_OUTPUT"
fi

info "Promoting admin to site administrator"
docker compose exec -T odk-service \
  node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-promote 2>/dev/null || {
  info "(Already promoted - continuing)"
}

# Source ODK API helper functions
source ./scripts/odk/odk-api-helper.sh

# Override for external access from host (use HTTPS with self-signed cert)
export ODK_API_BASE="https://localhost:8443/v1"

# Create a temporary curl wrapper to add -k and Host header
# (needed because ODK Central uses HTTPS with self-signed cert and SNI)
CURL_WRAPPER_DIR="/tmp/odk-curl-wrapper-$$"
mkdir -p "$CURL_WRAPPER_DIR"
cat > "$CURL_WRAPPER_DIR/curl" <<'CURL_EOF'
#!/bin/bash
exec /usr/bin/curl -k -H "Host: odk.local" "$@"
CURL_EOF
chmod +x "$CURL_WRAPPER_DIR/curl"

# Put wrapper first in PATH
export PATH="$CURL_WRAPPER_DIR:$PATH"

info "Authenticating as admin via HTTP API"
ADMIN_TOKEN=$(odk_login "${ODK_ADMIN_EMAIL}" "${ODK_ADMIN_PASSWORD}")

info "Creating project 'GLOW Development'"
PROJECT_ID=$(odk_create_project "GLOW Development" "${ADMIN_TOKEN}")

info "Creating API user: ${ODK_API_EMAIL}"
API_ACTOR_ID=$(odk_create_user "${ODK_API_EMAIL}" "${ODK_API_PASSWORD}" "${ADMIN_TOKEN}")

info "Assigning manager role to API user (for data seeding)"
odk_assign_role "${PROJECT_ID}" "${API_ACTOR_ID}" "1" "${ADMIN_TOKEN}"

# Step 8: Prepare & Seed Data
SEED_DIR="./data/mock_seed"
MANIFEST_PATH="${SEED_DIR}/manifest.csv"

if [[ ! -f "${MANIFEST_PATH}" && -f ./data/glow_base.csv ]]; then
  step "Transforming canonical base data"

  python ./scripts/odk/transform_mock_data.py \
    --input ./data/glow_base.csv \
    --output-dir "${SEED_DIR}" \
    --forms-dir ./odk-forms
fi

if [[ ! -f "${MANIFEST_PATH}" ]]; then
  step "Test data not found"
  
  warn "No transformed mock data found at ${MANIFEST_PATH}"
  echo ""
  echo "   To generate and transform canonical mock data:"
  echo ""
  echo "   (requires the glow-dummies repo cloned as a sibling checkout:"
  echo "    git clone https://github.com/OxfordRSE/glow-dummies.git ../glow-dummies"
  echo "    cd ../glow-dummies && julia --project=. -e 'import Pkg; Pkg.instantiate()' && cd -)"
  echo ""
  echo "   julia ../glow-dummies/bin/glow_dummies \\"
  echo "     --config ../glow-dummies/examples/glow_model.toml \\"
  echo "     --seed 42 \\"
  echo "     --output csv \\"
  echo "     > data/glow_base.csv"
  echo ""
  echo "   python scripts/odk/transform_mock_data.py \\"
  echo "     --input data/glow_base.csv \\"
  echo "     --output-dir data/mock_seed \\"
  echo "     --forms-dir odk-forms"
  echo ""
  echo "   Then run: docker compose restart api"
  echo "   And run: docker compose exec api uv run glow-api schools sync"
  echo ""
  
  SKIP_SEED=true
else
  step "Seeding test data${LIMIT:+ (limit: $LIMIT rows)}"
  
  uv run ./scripts/odk/seed_odk_test_data.py \
    --seed-dir "${SEED_DIR}" \
    --manifest "${MANIFEST_PATH}" \
    --forms-dir ./odk-forms \
    --odk-url https://localhost:8443 \
    --email "${ODK_API_EMAIL}" \
    --password "${ODK_API_PASSWORD}" \
    --project-id "${PROJECT_ID}" \
    ${LIMIT:+--limit $LIMIT}

  python ./scripts/odk/rewrite_odk_submission_timestamps.py \
    --manifest "${MANIFEST_PATH}"
  
  SKIP_SEED=false
fi

# Step 9: Start Glow API & Configure
step "Starting Glow API"

# Start API (credentials already in .env from Step 4)
info "Starting API container"
docker compose up -d --build api

wait_for_api

# Step 10: Create Glow Admin & Extract Schools
step "Configuring Glow API"

info "Creating Glow admin user: ${GLOW_ADMIN_USER}"
docker compose exec -T api glow-api users create ${GLOW_ADMIN_USER} \
  --password ${GLOW_ADMIN_PASSWORD} \
  --admin 2>/dev/null || {
  info "(User already exists - continuing)"
}

if [[ ${SKIP_SEED} != true ]]; then
  info "Extracting schools from data and creating neighbor relationships"
  docker compose exec -T api glow-api schools sync
else
  info "Skipping school extraction (no data seeded)"
fi

# Final: Show Success Summary
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ Development environment initialized successfully!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "📊 Services:"
echo "   Dashboard:    http://localhost:3000"
echo "   API:          http://localhost:8000"
echo "   API Docs:     http://localhost:8000/docs"
echo "   ODK Central:  http://localhost:8080"
echo ""
echo "🔑 Credentials:"
echo "   Glow Admin:   ${GLOW_ADMIN_USER} / ${GLOW_ADMIN_PASSWORD}"
echo "   ODK Admin:    ${ODK_ADMIN_EMAIL} / ${ODK_ADMIN_PASSWORD}"
echo "   ODK API:      ${ODK_API_EMAIL} / ${ODK_API_PASSWORD}"
echo ""
echo "   (Full credentials saved in .env.dev)"
echo ""
echo "📋 ODK Central:"
echo "   Project ID:   ${PROJECT_ID}"
if [[ ${SKIP_SEED} != true ]]; then
  echo "   Status:       ✅ Data seeded"
else
  echo "   Status:       ⚠️  No data seeded - follow instructions above"
fi
echo ""
echo "🚀 Next Steps:"
echo "   1. Start the dashboard: docker compose up -d dashboard"
echo "   2. Visit http://localhost:3000 to access the dashboard"
echo "   3. Log in with: ${GLOW_ADMIN_USER} / ${GLOW_ADMIN_PASSWORD}"
echo ""
echo "💡 Tips:"
echo "   - For subsequent development: docker compose up"
echo "   - Re-run with --reset to wipe all data and start fresh"
echo "   - Use --limit 100 for faster seeding (100 rows instead of all)"
echo "   - Check API logs: docker compose logs -f api"
echo "   - Run migrations: docker compose exec api uv run alembic upgrade head"
echo ""
echo "════════════════════════════════════════════════════════════════"

# Cleanup
rm -rf "$CURL_WRAPPER_DIR" 2>/dev/null || true
