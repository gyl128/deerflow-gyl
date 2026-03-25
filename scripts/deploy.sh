#!/usr/bin/env bash

set -euo pipefail

CMD="${1:-up}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="$REPO_ROOT/.env.production"
DOCKER_DIR="$REPO_ROOT/docker"
COMPOSE_CMD=(docker compose --env-file "$ENV_FILE" -p deer-flow -f "$DOCKER_DIR/docker-compose.yaml")

export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
export DEER_FLOW_REPO_ROOT="$REPO_ROOT"
export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$REPO_ROOT/config.production.yaml}"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$REPO_ROOT/extensions_config.production.json}"
export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"

if [ "$CMD" = "down" ]; then
    "${COMPOSE_CMD[@]}" down --remove-orphans
    exit 0
fi

"$REPO_ROOT/scripts/preflight-production.sh"

echo "=========================================="
echo "  DeerFlow Production Deployment (WSL)"
echo "=========================================="
echo ""
echo "Config:     $DEER_FLOW_CONFIG_PATH"
echo "Env file:   $ENV_FILE"
echo "Data dir:   $DEER_FLOW_HOME"
echo ""

"${COMPOSE_CMD[@]}" up --build -d --remove-orphans

echo ""
echo "Waiting for production smoke test..."
"$REPO_ROOT/scripts/smoke-production.sh"

echo ""
echo "=========================================="
echo "  DeerFlow production is running"
echo "=========================================="
echo ""
echo "  URL: http://localhost:${PORT:-2026}"
echo "  Logs: docker compose --env-file .env.production -p deer-flow -f docker/docker-compose.yaml logs -f"
echo "  Down: make down"

