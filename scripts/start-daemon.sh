#!/usr/bin/env bash
#
# start-daemon.sh - Start DeerFlow in daemon mode (background)
#
# Usage: ./scripts/start-daemon.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

PID_DIR="$REPO_ROOT/.pid"
mkdir -p "$PID_DIR"

LANGGRAPH_PORT="${DEER_FLOW_LANGGRAPH_PORT:-2024}"
GATEWAY_PORT="${DEER_FLOW_GATEWAY_PORT:-8001}"
FRONTEND_PORT="${DEER_FLOW_FRONTEND_PORT:-3001}"
NGINX_PORT="${DEER_FLOW_NGINX_PORT:-2026}"
BACKEND_RUNNER="$REPO_ROOT/backend/scripts/run-module.sh"
LANGGRAPH_RUNNER="$REPO_ROOT/backend/scripts/run-langgraph.sh"

check_service() {
    local name="$1"
    local url="$2"
    local timeout="$3"
    local log_file="$4"

    if "$REPO_ROOT/scripts/wait-for-http.sh" "$url" "$timeout" "$name"; then
        echo "[ok] $name healthy: $url"
        return 0
    fi

    echo "[fail] $name failed health check: $url"
    if [ -f "$log_file" ]; then
        echo "   log: $log_file"
        tail -40 "$log_file"
    fi
    return 1
}

echo "=========================================="
echo "  Starting DeerFlow in Daemon Mode"
echo "=========================================="
echo ""

./scripts/stop-services.sh

if [ ! -x "$BACKEND_RUNNER" ]; then
    echo "[fail] Backend runner not found: $BACKEND_RUNNER"
    exit 1
fi
if [ ! -x "$LANGGRAPH_RUNNER" ]; then
    echo "[fail] LangGraph runner not found: $LANGGRAPH_RUNNER"
    exit 1
fi

echo "Starting backend (LangGraph API)..."
cd "$REPO_ROOT/backend"
setsid bash -lc "cd '$REPO_ROOT/backend' && exec '$LANGGRAPH_RUNNER' prod --host 0.0.0.0 --port $LANGGRAPH_PORT" \
    > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_DIR/backend.pid"
echo "  Backend PID: $BACKEND_PID"

echo "Starting Gateway..."
setsid bash -lc "cd '$REPO_ROOT/backend' && exec '$BACKEND_RUNNER' uvicorn app.gateway.app:app --host 0.0.0.0 --port $GATEWAY_PORT" \
    > "$LOG_DIR/gateway.log" 2>&1 < /dev/null &
GATEWAY_PID=$!
echo $GATEWAY_PID > "$PID_DIR/gateway.pid"
echo "  Gateway PID: $GATEWAY_PID"

echo "Starting frontend..."
cd "$REPO_ROOT/frontend"
if [ ! -e "$REPO_ROOT/node_modules" ]; then
    ln -s "$REPO_ROOT/frontend/node_modules" "$REPO_ROOT/node_modules"
fi
if command -v pnpm >/dev/null 2>&1; then
    FRONTEND_PM="pnpm"
else
    FRONTEND_PM="corepack pnpm"
fi
if [ ! -f "$REPO_ROOT/frontend/.next/BUILD_ID" ]; then
    echo "  Building frontend for production..."
    bash -lc "cd '$REPO_ROOT/frontend' && rm -f .next/lock .next/dev/lock 2>/dev/null || true && $FRONTEND_PM exec next build" \
        > "$LOG_DIR/frontend-build.log" 2>&1
fi
setsid bash -lc "cd '$REPO_ROOT/frontend' && rm -f .next/lock .next/dev/lock 2>/dev/null || true && exec $FRONTEND_PM exec next start --hostname 0.0.0.0 --port $FRONTEND_PORT" \
    > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
FRONTEND_PID=$!
echo $FRONTEND_PID > "$PID_DIR/frontend.pid"
echo "  Frontend PID: $FRONTEND_PID"

echo "Starting nginx..."
nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT"
echo "  nginx started"

echo ""
echo "Waiting for services to be ready..."

echo ""
echo "=========================================="
echo "  DeerFlow Daemon Status"
echo "=========================================="
echo ""

FAILURES=0

check_service "LangGraph" "http://127.0.0.1:${LANGGRAPH_PORT}/docs" 90 "$LOG_DIR/backend.log" || FAILURES=1
check_service "Gateway" "http://127.0.0.1:${GATEWAY_PORT}/health" 60 "$LOG_DIR/gateway.log" || FAILURES=1
check_service "Frontend" "http://127.0.0.1:${FRONTEND_PORT}/" 120 "$LOG_DIR/frontend.log" || FAILURES=1
check_service "nginx" "http://127.0.0.1:${NGINX_PORT}/health" 30 "$LOG_DIR/nginx.log" || FAILURES=1

if [ "$FAILURES" -ne 0 ]; then
    echo ""
    echo "Daemon startup failed. See logs in $LOG_DIR/"
    exit 1
fi

echo ""
echo "Access URL: http://localhost:${NGINX_PORT}"
echo ""
echo "Logs: $LOG_DIR/"
echo "PIDs: $PID_DIR/"
echo ""
echo "To stop: make stop"
echo "=========================================="
