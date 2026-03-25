#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

stop_pattern() {
    pkill -f "$1" 2>/dev/null || true
}

echo "Stopping existing services..."
stop_pattern "langgraph dev"
stop_pattern "python -m langgraph_cli dev"
stop_pattern "uvicorn app.gateway.app:app"
stop_pattern "python -m uvicorn app.gateway.app:app"
stop_pattern "next dev"
stop_pattern "next build"
stop_pattern "next start"
stop_pattern "next-server"
stop_pattern "pnpm exec next"
stop_pattern "node_modules/next/dist/bin/next"
stop_pattern "corepack pnpm"

nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
sleep 1
pkill -9 nginx 2>/dev/null || true
killall -9 nginx 2>/dev/null || true

echo "Cleaning up sandbox containers..."
./scripts/cleanup-containers.sh deer-flow-sandbox 2>/dev/null || true
echo "✓ All services stopped"
