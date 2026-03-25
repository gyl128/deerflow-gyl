#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="deerflow-compose.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
START_SCRIPT="$ROOT_DIR/scripts/start-production-compose.sh"
ENV_FILE="${DEER_FLOW_COMPOSE_ENV_FILE:-$ROOT_DIR/.env.production}"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Run as root inside WSL." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required but unavailable." >&2
    exit 1
fi

if [ ! -x "$START_SCRIPT" ]; then
    echo "Start script missing or not executable: $START_SCRIPT" >&2
    exit 1
fi

cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=DeerFlow Docker Compose Stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$ROOT_DIR/docker
Environment=HOME=/root
Environment=DEER_FLOW_REPO_ROOT=$ROOT_DIR
Environment=COMPOSE_PROJECT_NAME=deer-flow
Environment=DEER_FLOW_COMPOSE_ENV_FILE=$ENV_FILE
ExecStart=$START_SCRIPT
ExecStop=/bin/bash -lc 'cd $ROOT_DIR/docker && HOME=/root DEER_FLOW_REPO_ROOT=$ROOT_DIR COMPOSE_PROJECT_NAME=deer-flow /usr/bin/docker compose -p deer-flow --env-file $ENV_FILE stop'
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME"
