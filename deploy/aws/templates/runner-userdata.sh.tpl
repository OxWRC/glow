#!/usr/bin/env bash
set -euo pipefail

exec > >(tee -a /var/log/glow-runner-bootstrap.log) 2>&1

mkdir -p /opt/glow-runner
rm -f /opt/glow-runner/bootstrap.ready

echo "[PROGRESS] Start bootstrap"

AWS_REGION="${aws_region}"
DOMAIN_NAME="${domain_name}"
GIT_REPO_URL="$${GIT_REPO_URL:-${git_repo_url}}"
GIT_REF="$${GIT_REF:-${git_ref}}"
GIT_COMMIT="$${GIT_COMMIT:-${git_checkout_ref}}"
CLOUDWATCH_BOOTSTRAP_LOG_GROUP="${cloudwatch_bootstrap_log_group}"
CLOUDWATCH_CONTAINERS_LOG_GROUP="${cloudwatch_containers_log_group}"
CLOUDWATCH_SYSTEM_LOG_GROUP="${cloudwatch_system_log_group}"

TOKEN=$(curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -fsS -H "X-aws-ec2-metadata-token: $${TOKEN}" \
  http://169.254.169.254/latest/meta-data/instance-id)

echo "[PROGRESS] Ensure swap"
# Small instance types (t3.small et al) don't have enough RAM to build the
# dashboard's vite SSR bundle while the existing stack is still running (an
# update rebuilds images before swapping them in). A swapfile absorbs that
# build-time burst instead of the box OOM-thrashing. Idempotent: reruns on
# every update via rerun_runner_userdata.
if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon --show=NAME --noheadings | grep -qx /swapfile || swapon /swapfile
grep -qx '/swapfile none swap sw 0 0' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "[PROGRESS] Set up CloudWatch Agent"

cloud_init_config=/opt/aws/amazon-cloudwatch-agent/etc/cloud_init.json
cloud_glow_config=/opt/aws/amazon-cloudwatch-agent/etc/glow.json

cat > $${cloud_glow_config} <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/glow-runner-bootstrap.log",
            "log_group_name": "$${CLOUDWATCH_BOOTSTRAP_LOG_GROUP}",
            "log_stream_name": "$${INSTANCE_ID}/runner-bootstrap"
          },
          {
            "file_path": "/var/log/messages",
            "log_group_name": "$${CLOUDWATCH_SYSTEM_LOG_GROUP}",
            "log_stream_name": "$${INSTANCE_ID}/messages"
          }
        ]
      }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a stop || true
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c "file:$${cloud_init_config}"
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a append-config \
  -m ec2 \
  -s \
  -c file:$${cloud_glow_config}

echo "[PROGRESS] Configure docker logging"

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "$${AWS_REGION}",
    "awslogs-group": "$${CLOUDWATCH_CONTAINERS_LOG_GROUP}",
    "awslogs-create-group": "false",
    "tag": "$${INSTANCE_ID}-{{.Name}}"
  }
}
EOF

systemctl restart docker

echo "[PROGRESS] Checking persistent state directory"
install -d -m 0755 /var/lib/glow
touch /var/lib/glow/.mnttest
rm -f /var/lib/glow/.mnttest

echo "[PROGRESS] Write /etc/glow-runner.env"

cat > /etc/glow-runner.env <<EOF
AWS_REGION=$${AWS_REGION}
DOMAIN_NAME=$${DOMAIN_NAME}
GIT_REPO_URL=$${GIT_REPO_URL}
GIT_REF=$${GIT_REF}
GIT_COMMIT=$${GIT_COMMIT}
CLOUDWATCH_CONTAINERS_LOG_GROUP=$${CLOUDWATCH_CONTAINERS_LOG_GROUP}
EOF

tmp_environment_file="$(mktemp)"
if [[ -f /etc/environment ]]; then
  grep -vE '^(GIT_REPO_URL|GIT_REF|GIT_COMMIT)=' /etc/environment > "$${tmp_environment_file}" || true
fi
cat >> "$${tmp_environment_file}" <<EOF
GIT_REPO_URL="$${GIT_REPO_URL}"
GIT_REF="$${GIT_REF}"
GIT_COMMIT="$${GIT_COMMIT}"
EOF
install -m 0644 "$${tmp_environment_file}" /etc/environment
rm -f "$${tmp_environment_file}"

if [[ ! -d /opt/glow/.git ]]; then
  touch /opt/glow-runner/bootstrap.ready
  echo "[PROGRESS] Repository checkout not present yet; waiting for deploy.py to prepare it"
  exit 0
fi

echo "[PROGRESS] Activate stack"
DOMAIN_NAME="$${DOMAIN_NAME}" bash /opt/glow/deploy/aws/runtime/activate-stack.sh

touch /opt/glow-runner/bootstrap.ready

echo "[SUCCESS] Runner bootstrap complete"
