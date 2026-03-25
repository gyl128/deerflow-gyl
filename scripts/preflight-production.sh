#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.production"
CONFIG_FILE="${DEER_FLOW_CONFIG_PATH:-$REPO_ROOT/config.production.yaml}"
EXT_FILE="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$REPO_ROOT/extensions_config.production.json}"
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
PORT="${PORT:-2026}"
SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
MODEL_PROXY_URL="${MODEL_PROXY_URL:-http://host.docker.internal:8787/v1}"

fail() {
    echo "[fail] $1" >&2
    exit 1
}

[ -f "$ENV_FILE" ] || fail "Missing $ENV_FILE. Copy .env.production.example to .env.production and fill required values."
[ -f "$CONFIG_FILE" ] || fail "Missing production config: $CONFIG_FILE"
[ -f "$EXT_FILE" ] || fail "Missing extensions config: $EXT_FILE"
[ -S "$SOCKET" ] || fail "Docker socket not found: $SOCKET"
command -v docker >/dev/null 2>&1 || fail "docker is not installed in WSL"
docker info >/dev/null 2>&1 || fail "docker daemon is not reachable from WSL"

mkdir -p "$DEER_FLOW_HOME"
[ -w "$DEER_FLOW_HOME" ] || fail "Data dir is not writable: $DEER_FLOW_HOME"

for key in BETTER_AUTH_SECRET POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_CONNECTION_STRING FEISHU_APP_ID FEISHU_APP_SECRET; do
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        fail "Missing ${key} in $ENV_FILE"
    fi
done

if grep -q 'replace-with-' "$ENV_FILE"; then
    fail "$ENV_FILE still contains placeholder values"
fi

if ss -ltn "sport = :$PORT" | grep -q LISTEN; then
    fail "Host port $PORT is already in use inside WSL"
fi

docker run --rm --add-host host.docker.internal:host-gateway curlimages/curl:8.12.1 -fsS "$MODEL_PROXY_URL/models" >/dev/null 2>&1 \
    || fail "Model proxy is not reachable from containers: $MODEL_PROXY_URL"

echo "[ok] production preflight passed"
