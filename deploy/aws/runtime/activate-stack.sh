#!/usr/bin/env bash
set -euo pipefail

source /etc/glow-runner.env

DOMAIN_NAME="${DOMAIN_NAME:?DOMAIN_NAME is required}"
WORK_DIR="/opt/glow"
STATE_DIR="/var/lib/glow"
ADMIN_ENV="${STATE_DIR}/.deploy/.env.admin"
RUNTIME_ENV="${STATE_DIR}/.deploy/share/.env.runtime"
FORMS_STATE="${STATE_DIR}/.deploy/share/odk-forms-state.json"
FORMS_DIR="${WORK_DIR}/odk-forms"
export ODK_API_BASE="http://127.0.0.1:8080/v1"
export ODK_DOMAIN="odk.${DOMAIN_NAME}"

export BUILDKIT_PROGRESS=plain

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
step() { echo "[PROGRESS] $*"; }
error() { echo "[ERROR] $*" >&2; }

source "${WORK_DIR}/scripts/odk/odk-api-helper.sh"

prepare_data_layout() {
  step "Preparing persistent directory layout"
  mkdir -p "${STATE_DIR}/.deploy/share"
  mkdir -p "${STATE_DIR}/glow-postgres"
  mkdir -p "${STATE_DIR}/odk-postgres"
  mkdir -p "${STATE_DIR}/odk-transfer"
  mkdir -p "${STATE_DIR}/odk-secrets"
  mkdir -p "${STATE_DIR}/odk-enketo-redis-main"
  mkdir -p "${STATE_DIR}/odk-enketo-redis-cache"
  mkdir -p "${STATE_DIR}/api-audit-logs"

  mkdir -p "${WORK_DIR}/docker-mount-data"
  if [[ ! -L "${WORK_DIR}/docker-mount-data" ]]; then
    rm -rf "${WORK_DIR}/docker-mount-data"
    ln -s "${STATE_DIR}" "${WORK_DIR}/docker-mount-data"
  fi

  touch "${FORMS_STATE}"
  if [[ ! -s "${FORMS_STATE}" ]]; then
    echo '{}' > "${FORMS_STATE}"
  fi
}

generate_runtime_env() {
  if [[ -f "${RUNTIME_ENV}" ]]; then
    info "Runtime environment already exists"
    return
  fi

  step "Generating runtime secrets"
  local glow_secret
  glow_secret="$(openssl rand -base64 32 | tr -d '\n')"
  local postgres_password
  postgres_password="$(openssl rand -base64 32 | tr -d '\n')"
  local odk_postgres_password
  odk_postgres_password="$(openssl rand -base64 32 | tr -d '\n')"
  local odk_admin_password
  odk_admin_password="$(openssl rand -base64 48 | tr -d '\n')"
  local odk_api_password
  odk_api_password="$(openssl rand -base64 24 | tr -d '\n')"
  local admin_email="glow-admin@${DOMAIN_NAME}"
  local api_email="glow-api@${DOMAIN_NAME}"

  mkdir -p "$(dirname "${ADMIN_ENV}")" "$(dirname "${RUNTIME_ENV}")"
  cat > "${ADMIN_ENV}" <<EOF
ODK_ADMIN_EMAIL=${admin_email}
ODK_ADMIN_PASSWORD=${odk_admin_password}
EOF
  chmod 600 "${ADMIN_ENV}"

  cat > "${RUNTIME_ENV}" <<EOF
GLOW_SECRET_KEY=${glow_secret}
GLOW_MIN_N=5
GLOW_ODK_API_URL=https://nginx
GLOW_ODK_VERIFY_SSL=false
GLOW_ODK_API_EMAIL=${api_email}
GLOW_ODK_API_PASSWORD=${odk_api_password}
GLOW_ODK_PROJECT_ID=1
GLOW_ODK_FORM_ID=bewell_questionnaire
GLOW_DATA_CACHE_PATH=.cache.parquet
GLOW_DATA_REFRESH_HOURS=1
GLOW_METADATA_DATABASE_URL=postgresql+psycopg://glow:${postgres_password}@api-db:5432/glow
GLOW_CORS_ORIGINS=["http://${DOMAIN_NAME}","https://${DOMAIN_NAME}","http://api.${DOMAIN_NAME}","https://api.${DOMAIN_NAME}"]
PUBLIC_API_BASE=//api.${DOMAIN_NAME}
POSTGRES_DB=glow
POSTGRES_USER=glow
POSTGRES_PASSWORD=${postgres_password}
ODK_DOMAIN=odk.${DOMAIN_NAME}
ODK_SYSADMIN_EMAIL=admin@${DOMAIN_NAME}
ODK_SSL_TYPE=upstream
ODK_HTTP_PORT=8080
ODK_HTTPS_PORT=8443
ODK_POSTGRES_DB=odk
ODK_POSTGRES_USER=odk
ODK_POSTGRES_PASSWORD=${odk_postgres_password}
ODK_CENTRAL_TAG=v2026.1.2
ODK_API_EMAIL=${api_email}
ODK_API_PASSWORD=${odk_api_password}
ODK_API_URL=http://odk.${DOMAIN_NAME}
EOF
  chmod 600 "${RUNTIME_ENV}"
}

sync_cognito_env() {
  # Unlike generate_runtime_env(), this runs on every activation (fresh
  # deploy or update): the Cognito values come from Terraform outputs, not
  # generated secrets, so refreshing them here carries none of the
  # credential-rotation risk that keeps generate_runtime_env() guarded to a
  # one-time run. Dedupe-and-append keeps this idempotent without touching
  # any other line (in particular, the generated secrets) already in
  # RUNTIME_ENV.
  step "Syncing Cognito config into runtime environment"
  local tmp
  tmp="$(mktemp)"
  grep -vE '^(GLOW_COGNITO_USER_POOL_ID|GLOW_COGNITO_CLIENT_ID|GLOW_COGNITO_REGION|PUBLIC_COGNITO_CLIENT_ID|PUBLIC_COGNITO_DOMAIN|PUBLIC_COGNITO_REGION)=' \
    "${RUNTIME_ENV}" > "${tmp}" || true
  cat >> "${tmp}" <<EOF
GLOW_COGNITO_USER_POOL_ID=${GLOW_COGNITO_USER_POOL_ID:-}
GLOW_COGNITO_CLIENT_ID=${GLOW_COGNITO_CLIENT_ID:-}
GLOW_COGNITO_REGION=${GLOW_COGNITO_REGION:-}
PUBLIC_COGNITO_CLIENT_ID=${GLOW_COGNITO_CLIENT_ID:-}
PUBLIC_COGNITO_DOMAIN=${GLOW_COGNITO_DOMAIN:-}
PUBLIC_COGNITO_REGION=${GLOW_COGNITO_REGION:-}
EOF
  install -m 0600 "${tmp}" "${RUNTIME_ENV}"
  rm -f "${tmp}"
}

compose() {
  docker compose --profile odk --env-file "${RUNTIME_ENV}" -f "$WORK_DIR/compose.yml" "$@"
}

compute_app_version() {
  step "Determining deployed app version"
  APP_VERSION="$(git -C "${WORK_DIR}" describe --tags --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || echo unknown)"
  export APP_VERSION
  info "App version: ${APP_VERSION}"
}

start_stack() {
  step "Building and starting containers"
  cd "${WORK_DIR}"
  step "Building API"
  compose --progress plain build api
  step "Building Dashboard"
  compose --progress plain build dashboard

  step "Bringing up all containers"
  compose --progress quiet up -d --build --quiet-pull --quiet-build --remove-orphans
}

wait_for_odk() {
  step "Waiting for ODK service"
  info "> odk_ping"
  local retries=60
  while [[ ${retries} -gt 0 ]]; do
    if odk_ping >/dev/null 2>&1; then
      info "ODK service is ready"
      return
    fi
    sleep 10
    retries=$((retries - 1))
  done
  error "ODK service did not become ready"
  exit 1
}

configure_odk() {
  step "Configuring ODK users and project"
  source "${ADMIN_ENV}"
  source "${RUNTIME_ENV}"

  local create_output
  create_output="$(printf '%s\n' "${ODK_ADMIN_PASSWORD}" | compose exec -T odk-service \
    node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-create 2>&1 || true)"

  if printf '%s' "${create_output}" | grep -qi "already exists"; then
    info "ODK admin user already exists; reconciling password"
    printf '%s\n' "${ODK_ADMIN_PASSWORD}" | compose exec -T odk-service \
      node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-set-password >/dev/null
  elif printf '%s' "${create_output}" | grep -qiE '(error|failed)'; then
    error "Failed to create ODK admin user: ${create_output}"
    exit 1
  else
    info "ODK admin user created"
  fi

  compose exec -T odk-service node /usr/odk/lib/bin/cli.js -u "${ODK_ADMIN_EMAIL}" user-promote 2>/dev/null || {
    info "(Already promoted - continuing)"
  }

  local token
  token="$(odk_login "${ODK_ADMIN_EMAIL}" "${ODK_ADMIN_PASSWORD}")"
  local project_id
  project_id="$(odk_create_project "GLOW Data Collection" "${token}")"
  local actor_id
  actor_id="$(odk_create_user "${ODK_API_EMAIL}" "${ODK_API_PASSWORD}" "${token}")"
  odk_assign_role "${project_id}" "${actor_id}" "2" "${token}"
}

process_forms() {
  if [[ ! -d "${FORMS_DIR}" ]]; then
    info "No forms directory present; skipping upload"
    return
  fi

  step "Uploading ODK forms if they changed"
  source "${ADMIN_ENV}"
  local token
  token="$(odk_login "${ODK_ADMIN_EMAIL}" "${ODK_ADMIN_PASSWORD}")"
  local project_id
  project_id="$(odk_get_project_by_name "GLOW Data Collection" "${token}")"

  local forms_state
  forms_state="$(cat "${FORMS_STATE}")"

  while IFS= read -r -d '' form_file; do
    local xml_content
    if [[ "${form_file}" == *.xls || "${form_file}" == *.xlsx ]]; then
      xml_content="$(convert_xlsform_to_xml "${form_file}")"
    else
      xml_content="$(cat "${form_file}")"
    fi

    local xml_form_id
    xml_form_id="$(extract_xmlformid_from_xml "${xml_content}")"
    local current_hash
    current_hash="$(printf '%s' "${xml_content}" | sha256sum | cut -d' ' -f1)"
    local stored_hash
    stored_hash="$(printf '%s' "${forms_state}" | jq -r --arg id "${xml_form_id}" '.[$id].hash // empty')"

    if [[ "${stored_hash}" == "${current_hash}" ]]; then
      info "Skipping unchanged form ${xml_form_id}"
      continue
    fi

    odk_upload_form "${project_id}" "${xml_content}" "${token}" >/dev/null
    forms_state="$(printf '%s' "${forms_state}" | jq --arg id "${xml_form_id}" --arg hash "${current_hash}" '.[$id] = {hash: $hash}')"
    info "Uploaded form ${xml_form_id}"
  done < <(find "${FORMS_DIR}" -type f \( -name '*.xml' -o -name '*.xls' -o -name '*.xlsx' \) -print0)

  printf '%s\n' "${forms_state}" > "${FORMS_STATE}"
}

verify_stack() {
  step "Verifying service health"
  curl -fsS http://127.0.0.1:8000/health >/dev/null
  curl -fsS http://127.0.0.1:3000/en >/dev/null
  odk_ping >/dev/null
}

write_metadata() {
  step "Writing deployment metadata"
  local checkout_ref
  checkout_ref="$(git -C "${WORK_DIR}" rev-parse HEAD)"
  cat > "${STATE_DIR}/.glow-deployment.json" <<EOF
{
  "domain_name": "${DOMAIN_NAME}",
  "git_ref": "${GIT_REF:-}",
  "git_commit": "${checkout_ref}",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}

main() {
  step "Starting Glow stack activation"
  prepare_data_layout
  generate_runtime_env
  sync_cognito_env
  compute_app_version
  start_stack
  wait_for_odk
  configure_odk
  process_forms
  verify_stack
  write_metadata
  echo "[SUCCESS] Stack activation complete"
}

main "$@"
