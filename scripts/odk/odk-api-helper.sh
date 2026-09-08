#!/usr/bin/env bash
# odk-api-helper.sh — ODK Central HTTP API wrapper functions
#
# Provides functions to interact with ODK Central's HTTP API:
# - odk_login: Authenticate and get session token
# - odk_create_project: Create project if not exists
# - odk_get_project_by_name: Get project ID by name
# - odk_create_user: Create user if not exists
# - odk_get_user_actor_id: Get actor ID for user email
# - odk_assign_role: Assign role to user on project
# - odk_list_forms: List forms in project
# - odk_upload_form: Upload form to project
# - convert_xlsform_to_xml: Convert XLSForm to XForm XML
# - extract_xmlformid_from_xml: Extract xmlFormId from XML
#
# Usage:
#   source odk-api-helper.sh
#   ODK_SESSION_TOKEN=$(odk_login "admin@example.com" "password123")
#   PROJECT_ID=$(odk_get_project_by_name "My Project" "$ODK_SESSION_TOKEN")
#

set -euo pipefail

# ODK Central API base URL (internal Docker network)
ODK_API_BASE="${ODK_API_BASE:-http://service:8383/v1}"
# NOTE: must default from itself first -- a caller that exports
# ODK_HOST_HEADER before sourcing this file (as generate_seed_dump.sh does)
# needs that value to survive sourcing, not get silently clobbered back to
# empty because only ODK_DOMAIN was checked.
ODK_HOST_HEADER="${ODK_HOST_HEADER:-${ODK_DOMAIN:-}}"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RESET='\033[0m'

odk_info()  { echo -e "${GREEN}[ODK]${RESET} $*" >&2; }
odk_warn()  { echo -e "${YELLOW}[ODK WARN]${RESET} $*" >&2; }
odk_error() { echo -e "${RED}[ODK ERROR]${RESET} $*" >&2; }

odk_curl() {
  local -a extra_args=()
  if [[ -n "${ODK_HOST_HEADER}" ]]; then
    extra_args+=(-H "Host: ${ODK_HOST_HEADER}")
  fi
  if [[ -n "${ODK_CURL_INSECURE:-}" ]]; then
    extra_args+=(-k)
  fi
  curl "${extra_args[@]}" "$@"
}

odk_ping() {
  local root_url="${ODK_API_BASE%/v1}/"
  odk_curl -fsS "${root_url}"
}

# ─── Authentication ──────────────────────────────────────────────────────────

# odk_login: Authenticate with ODK Central and return session token
# Args: $1=email, $2=password
# Returns: session token (stdout)
# Exit code: 0 on success, 1 on failure
odk_login() {
  local email="$1"
  local password="$2"
  
  local response
  response=$(odk_curl -sf -X POST "${ODK_API_BASE}/sessions" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg email "$email" --arg password "$password" \
      '{email: $email, password: $password}')" 2>&1) || {
    odk_error "Failed to authenticate as ${email}"
    return 1
  }
  
  local token
  token=$(echo "$response" | jq -r '.token // empty')
  
  if [[ -z "$token" ]]; then
    odk_error "No token in login response"
    return 1
  fi
  
  echo "$token"
}

# ─── Project Management ──────────────────────────────────────────────────────

# odk_get_project_by_name: Get project ID by name
# Args: $1=project_name, $2=session_token
# Returns: project_id (stdout) or empty string if not found
# Exit code: 0 on success (even if not found), 1 on API error
odk_get_project_by_name() {
  local project_name="$1"
  local token="$2"
  
  local response
  response=$(odk_curl -sf -X GET "${ODK_API_BASE}/projects" \
    -H "Authorization: Bearer ${token}" 2>&1) || {
    odk_error "Failed to list projects"
    return 1
  }
  
  local project_id
  project_id=$(echo "$response" | jq -r \
    --arg name "$project_name" \
    '.[] | select(.name == $name) | .id // empty')
  
  echo "$project_id"
}

# odk_create_project: Create project if not exists
# Args: $1=project_name, $2=session_token
# Returns: project_id (stdout)
# Exit code: 0 on success, 1 on failure
odk_create_project() {
  local project_name="$1"
  local token="$2"
  
  # Check if project already exists
  local existing_id
  existing_id=$(odk_get_project_by_name "$project_name" "$token")
  
  if [[ -n "$existing_id" ]]; then
    odk_info "Project '${project_name}' already exists (ID: ${existing_id})"
    echo "$existing_id"
    return 0
  fi
  
  # Create new project
  local response
  response=$(odk_curl -sf -X POST "${ODK_API_BASE}/projects" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg name "$project_name" '{name: $name}')" 2>&1) || {
    odk_error "Failed to create project '${project_name}'"
    return 1
  }
  
  local project_id
  project_id=$(echo "$response" | jq -r '.id // empty')
  
  if [[ -z "$project_id" ]]; then
    odk_error "No project ID in create response"
    return 1
  fi
  
  odk_info "Created project '${project_name}' (ID: ${project_id})"
  echo "$project_id"
}

# ─── User Management ─────────────────────────────────────────────────────────

# odk_get_user_actor_id: Get actor ID for user by email
# Args: $1=email, $2=session_token
# Returns: actor_id (stdout) or empty string if not found
# Exit code: 0 on success (even if not found), 1 on API error
odk_get_user_actor_id() {
  local email="$1"
  local token="$2"
  
  local response
  response=$(odk_curl -sf -X GET "${ODK_API_BASE}/users" \
    -H "Authorization: Bearer ${token}" 2>&1) || {
    odk_error "Failed to list users"
    return 1
  }
  
  local actor_id
  actor_id=$(echo "$response" | jq -r \
    --arg email "$email" \
    '.[] | select(.email == $email) | .id // empty')
  
  echo "$actor_id"
}

# odk_create_user: Create user if not exists
# Args: $1=email, $2=password, $3=session_token
# Returns: actor_id (stdout)
# Exit code: 0 on success, 1 on failure
odk_create_user() {
  local email="$1"
  local password="$2"
  local token="$3"
  
  # Check if user already exists
  local existing_id
  existing_id=$(odk_get_user_actor_id "$email" "$token")
  
  if [[ -n "$existing_id" ]]; then
    odk_info "User '${email}' already exists (Actor ID: ${existing_id})"
    echo "$existing_id"
    return 0
  fi
  
  # Create new user
  local response
  response=$(odk_curl -sf -X POST "${ODK_API_BASE}/users" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg email "$email" --arg password "$password" \
      '{email: $email, password: $password}')" 2>&1) || {
    odk_error "Failed to create user '${email}'"
    return 1
  }
  
  local actor_id
  actor_id=$(echo "$response" | jq -r '.id // empty')
  
  if [[ -z "$actor_id" ]]; then
    odk_error "No actor ID in create user response"
    return 1
  fi
  
  odk_info "Created user '${email}' (Actor ID: ${actor_id})"
  echo "$actor_id"
}

# odk_assign_role: Assign role to user on project
# Args: $1=project_id, $2=actor_id, $3=role_id, $4=session_token
# Role IDs: 2=viewer, 4=data collector, etc.
# Exit code: 0 on success, 1 on failure
odk_assign_role() {
  local project_id="$1"
  local actor_id="$2"
  local role_id="$3"
  local token="$4"
  
  # Check if assignment already exists
  local response
  response=$(odk_curl -sf -X GET "${ODK_API_BASE}/projects/${project_id}/assignments" \
    -H "Authorization: Bearer ${token}" 2>&1) || {
    odk_warn "Failed to check existing role assignments"
  }
  
  # Check if user already has this role
  local existing_role
  existing_role=$(echo "$response" | jq -r \
    --arg actor_id "$actor_id" \
    --arg role_id "$role_id" \
    '.[] | select(.actor.id == ($actor_id | tonumber) and .roleId == ($role_id | tonumber)) | .roleId // empty' 2>/dev/null || true)
  
  if [[ -n "$existing_role" ]]; then
    odk_info "User (Actor ID: ${actor_id}) already has role ${role_id} on project ${project_id}"
    return 0
  fi
  
  # Assign role
  local assign_response
  assign_response=$(odk_curl -s -X POST "${ODK_API_BASE}/projects/${project_id}/assignments/${role_id}/${actor_id}" \
    -H "Authorization: Bearer ${token}" 2>&1)
  
  # Check HTTP status - parse from JSON error response
  if echo "$assign_response" | jq -e '.code' >/dev/null 2>&1; then
    local error_code
    error_code=$(echo "$assign_response" | jq -r '.code // empty')
    
    # 409.3 = already exists
    if [[ "$error_code" == "409.3" ]] || echo "$assign_response" | grep -qi "already exists"; then
      odk_info "User (Actor ID: ${actor_id}) already has role ${role_id} on project ${project_id}"
      return 0
    fi
    
    odk_error "Failed to assign role ${role_id} to actor ${actor_id} on project ${project_id}"
    return 1
  fi
  
  odk_info "Assigned role ${role_id} to actor ${actor_id} on project ${project_id}"
}

# ─── Form Management ─────────────────────────────────────────────────────────

# odk_list_forms: List forms in project
# Args: $1=project_id, $2=session_token
# Returns: JSON array of forms (stdout)
# Exit code: 0 on success, 1 on failure
odk_list_forms() {
  local project_id="$1"
  local token="$2"
  
  local response
  response=$(odk_curl -sf -X GET "${ODK_API_BASE}/projects/${project_id}/forms" \
    -H "Authorization: Bearer ${token}" 2>&1) || {
    odk_error "Failed to list forms in project ${project_id}"
    return 1
  }
  
  echo "$response"
}

# odk_upload_form: Upload form to project (creates new version if form exists)
# Args: $1=project_id, $2=xml_content, $3=session_token
# Returns: form xmlFormId (stdout)
# Exit code: 0 on success, 1 on failure
odk_upload_form() {
  local project_id="$1"
  local xml_content="$2"
  local token="$3"
  
  # POST or PUT depending on whether form exists
  # ODK Central creates a new version automatically if xmlFormId exists
  local response
  response=$(odk_curl -s -X POST "${ODK_API_BASE}/projects/${project_id}/forms?publish=true" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/xml" \
    -H "X-XmlFormId-Fallback: true" \
    --data-binary "$xml_content" 2>&1)
  
  # Check if response contains an error
  if echo "$response" | jq -e '.code' >/dev/null 2>&1; then
    local error_code
    error_code=$(echo "$response" | jq -r '.code // empty')
    
    # 409.3 = form already exists with same version
    if [[ "$error_code" == "409.3" ]]; then
      # Extract xmlFormId from the XML itself since error doesn't include it
      local xmlFormId
      xmlFormId=$(extract_xmlformid_from_xml "$xml_content")
      
      if [[ -n "$xmlFormId" ]]; then
        odk_info "Form already exists: ${xmlFormId}"
        echo "$xmlFormId"
        return 0
      fi
    fi
    
    odk_error "Failed to upload form to project ${project_id}: $(echo "$response" | jq -r '.message // empty')"
    return 1
  fi
  
  local xmlFormId
  xmlFormId=$(echo "$response" | jq -r '.xmlFormId // .formId // empty')
  
  if [[ -z "$xmlFormId" ]]; then
    odk_error "No xmlFormId in upload response"
    return 1
  fi
  
  odk_info "Form uploaded: ${xmlFormId}"
  echo "$xmlFormId"
}

# ─── Form Conversion ─────────────────────────────────────────────────────────

# convert_xlsform_to_xml: Convert XLSForm to XForm XML using pyxform container
# Args: $1=path_to_xlsform_file
# Returns: XML content (stdout)
# Exit code: 0 on success, 1 on failure
convert_xlsform_to_xml() {
  local xlsform_path="$1"
  
  if [[ ! -f "$xlsform_path" ]]; then
    odk_error "XLSForm file not found: ${xlsform_path}"
    return 1
  fi
  
  # Get the Docker Compose project name for the network
  local project_name
  project_name=$(docker compose config --format json | jq -r '.name // "glow"')
  local network_name="${project_name}_odk_internal"
  
  # Call pyxform via temporary curl container connected to odk_internal network
  local response
  response=$(docker run --rm --network "${network_name}" -i curlimages/curl:latest \
    -sf -X POST http://pyxform/api/v1/convert \
    -H "Content-Type: application/octet-stream" \
    --data-binary @- < "${xlsform_path}" 2>&1) || {
    odk_error "Failed to convert XLSForm: ${xlsform_path}"
    return 1
  }
  
  local status
  status=$(echo "$response" | jq -r '.status // empty')
  
  if [[ "$status" != "200" ]]; then
    local error_msg
    error_msg=$(echo "$response" | jq -r '.error // "Unknown error"')
    odk_error "XLSForm conversion failed: ${error_msg}"
    return 1
  fi
  
  local xml
  xml=$(echo "$response" | jq -r '.result // empty')
  
  if [[ -z "$xml" ]]; then
    odk_error "No XML in conversion response"
    return 1
  fi
  
  # Check for warnings
  local warnings
  warnings=$(echo "$response" | jq -r '.warnings // empty')
  if [[ -n "$warnings" && "$warnings" != "null" ]]; then
    odk_warn "XLSForm conversion warnings for ${xlsform_path}:"
    echo "$warnings" | jq -r '.[]' >&2
  fi
  
  echo "$xml"
}

# extract_xmlformid_from_xml: Extract xmlFormId from XForm XML
# Args: $1=xml_content
# Returns: xmlFormId (stdout)
# Exit code: 0 on success, 1 on failure
extract_xmlformid_from_xml() {
  local xml_content="$1"
  
  local xmlformid
  # Try xmllint first (if available), otherwise use grep
  if command -v xmllint &>/dev/null; then
    xmlformid=$(echo "$xml_content" | xmllint --xpath 'string(//*[local-name()="data"]/@id)' - 2>/dev/null || true)
  else
    # Fallback: extract using grep (matches <data id="formid">)
    xmlformid=$(echo "$xml_content" | grep -oP '<data\s+id="\K[^"]+' | head -1)
  fi
  
  if [[ -z "$xmlformid" ]]; then
    odk_error "Could not extract xmlFormId from XML"
    return 1
  fi
  
  echo "$xmlformid"
}
