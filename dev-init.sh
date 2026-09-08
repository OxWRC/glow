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
#   ./dev-init.sh --reset      # Wipe everything and start fresh
#   ./dev-init.sh --help       # Show this help

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RESET=false

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
  --reset       Wipe all data volumes and start fresh
  --help        Show this help message

Examples:
  # Initialize with all test data
  ./dev-init.sh

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

step "Writing fixed dev credentials"

# odk-central/postgres/README.md documents these -- they're baked into the
# dev image's seed data at build time (scripts/odk/generate_seed_dump.sh),
# not generated per-run. GLOW_ADMIN_USER/PASSWORD stay simple local-only
# values, unrelated to the baked ODK data.
ODK_ADMIN_EMAIL="admin@glow.local"
ODK_ADMIN_PASSWORD="devpassword"
ODK_API_EMAIL="api@glow.local"
ODK_API_PASSWORD="devpassword"
GLOW_ADMIN_USER="admin"
GLOW_ADMIN_PASSWORD="admin"

cat > .env.dev <<EOF
# Auto-generated by dev-init.sh - DO NOT COMMIT
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# ODK Central Admin (fixed dev credentials -- see odk-central/postgres/README.md)
ODK_ADMIN_EMAIL=${ODK_ADMIN_EMAIL}
ODK_ADMIN_PASSWORD=${ODK_ADMIN_PASSWORD}

# ODK Central API User (for Glow API)
ODK_API_EMAIL=${ODK_API_EMAIL}
ODK_API_PASSWORD=${ODK_API_PASSWORD}

# Glow API Admin
GLOW_ADMIN_USER=${GLOW_ADMIN_USER}
GLOW_ADMIN_PASSWORD=${GLOW_ADMIN_PASSWORD}
EOF

info "Credentials ready: ${ODK_ADMIN_EMAIL}"

# Update .env with credentials needed by docker-compose
# (Do this early so they're available when containers start)
if grep -q "# ODK Central Integration" .env 2>/dev/null; then
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

info "Extracting schools from data and creating neighbor relationships"
# --no-create-users: the per-school user-creation branch hits a
# DetachedInstanceError on first run against fresh data (glow_api/cli.py
# schools_sync iterates a stale, session-closed school list) -- pre-existing
# app bug, out of scope here. Neighbor sync and admin access still run.
docker compose exec -T api glow-api schools sync --no-create-users

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
echo "   Project ID:   1"
echo "   Status:       ✅ Data seeded (baked into the postgres14 dev image)"
echo ""
echo "🚀 Next Steps:"
echo "   1. Start the dashboard: docker compose up -d dashboard"
echo "   2. Visit http://localhost:3000 to access the dashboard"
echo "   3. Log in with: ${GLOW_ADMIN_USER} / ${GLOW_ADMIN_PASSWORD}"
echo ""
echo "💡 Tips:"
echo "   - For subsequent development: docker compose up"
echo "   - Re-run with --reset to wipe all data and start fresh"
echo "   - Check API logs: docker compose logs -f api"
echo "   - Run migrations: docker compose exec api uv run alembic upgrade head"
echo ""
echo "════════════════════════════════════════════════════════════════"
