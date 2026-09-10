#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[runner-deps] %s\n' "$*" >&2
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  log "this script must run as root"
  exit 1
fi

log "updating system packages"
dnf update -y

log "installing base dependencies"
dnf install -y \
  awscli \
  docker \
  git \
  jq \
  rsync \
  amazon-cloudwatch-agent \
  amazon-ssm-agent

dnf clean all

log "enabling and starting amazon-ssm-agent"
systemctl enable amazon-ssm-agent
systemctl restart amazon-ssm-agent

log "verifying amazon-ssm-agent"
systemctl is-active --quiet amazon-ssm-agent

log "enabling and starting docker"
systemctl enable docker
systemctl start docker

log "ensuring docker group exists"
if ! getent group docker >/dev/null 2>&1; then
  groupadd docker
fi

log "adding ec2-user to docker group"
if id ec2-user >/dev/null 2>&1; then
  usermod -aG docker ec2-user
fi

log "installing docker compose and buildx plugins"
mkdir -p /usr/local/lib/docker/cli-plugins

compose_plugin="/usr/local/lib/docker/cli-plugins/docker-compose"
buildx_plugin="/usr/local/lib/docker/cli-plugins/docker-buildx"

# Prefer repo packages if available.
if dnf list --available docker-compose-plugin >/dev/null 2>&1; then
  dnf install -y docker-compose-plugin
else
  arch="$(uname -m)"
  case "$arch" in
    x86_64) compose_arch="x86_64" ;;
    aarch64) compose_arch="aarch64" ;;
    *)
      log "unsupported architecture for docker compose plugin: $arch"
      exit 1
      ;;
  esac
  COMPOSE_VERSION="${COMPOSE_VERSION:-v5.3.0}"

  compose_url="${DOCKER_COMPOSE_URL:-https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${compose_arch}}"
  log "compose_url=${compose_url}"
  curl -fsSL "$compose_url" -o "$compose_plugin"
  chmod 0755 "$compose_plugin"
fi

if dnf list --available docker-buildx-plugin >/dev/null 2>&1; then
  dnf install -y docker-buildx-plugin
else
  case "$(uname -m)" in
  x86_64) buildx_arch="amd64" ;;
  aarch64) buildx_arch="arm64" ;;
  *)
    log "unsupported architecture: $(uname -m)"
    exit 1
    ;;
  esac
  BUILDX_VERSION="${BUILDX_VERSION:-v0.35.0}"

  buildx_url="${DOCKER_BUILDX_URL:-https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-${buildx_arch}}"
  log "buildx_url=${buildx_url}"
  curl -fsSL "$buildx_url" -o "$buildx_plugin"
  chmod 0755 "$buildx_plugin"
fi

log "verifying docker installation"
docker --version
docker buildx version
docker compose version

log "runner dependencies installed"

log "enabling cloudwatch logging for cloud init"
systemctl enable amazon-cloudwatch-agent

cloud_init_config=/opt/aws/amazon-cloudwatch-agent/etc/cloud_init.json

sudo mkdir -p /opt/aws/amazon-cloudwatch-agent/etc

sudo tee "$cloud_init_config" >/dev/null <<'EOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/cloud-init-output.log",
            "log_group_name": "/debug/glow/cloud-init-output",
            "log_stream_name": "{instance_id}/cloud-init-output"
          },
          {
            "file_path": "/var/log/cloud-init.log",
            "log_group_name": "/debug/glow/cloud-init",
            "log_stream_name": "{instance_id}",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
EOF

sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c "file:${cloud_init_config}"

mkdir -p /opt/glow-runner
