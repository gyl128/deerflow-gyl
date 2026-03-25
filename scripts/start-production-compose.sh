#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="$ROOT_DIR/docker"
ENV_FILE="${DEER_FLOW_COMPOSE_ENV_FILE:-$ROOT_DIR/.env.production}"
export HOME="${HOME:-/root}"
export DEER_FLOW_REPO_ROOT="${DEER_FLOW_REPO_ROOT:-$ROOT_DIR}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-deer-flow}"
EXPECTED_CONTAINERS=(
  deer-flow-postgres
  deer-flow-langgraph
  deer-flow-gateway
  deer-flow-channel-worker
  deer-flow-frontend
  deer-flow-nginx
)

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required but not installed" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Compose env file not found: $ENV_FILE" >&2
    exit 1
fi

existing=0
for name in "${EXPECTED_CONTAINERS[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
        existing=$((existing + 1))
    fi
done

if [ "$existing" -eq "${#EXPECTED_CONTAINERS[@]}" ]; then
    docker start "${EXPECTED_CONTAINERS[@]}" >/dev/null
    exit 0
fi

if [ "$existing" -ne 0 ]; then
    echo "Partial DeerFlow container set detected; refusing to auto-reconcile. Run docker compose manually." >&2
    exit 1
fi

cd "$COMPOSE_DIR"
exec docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" up -d --remove-orphans
