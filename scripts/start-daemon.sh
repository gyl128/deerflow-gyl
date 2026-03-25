#!/usr/bin/env bash
#
# start-daemon.sh - Start DeerFlow in daemon mode (background)
#
# Usage: ./scripts/start-daemon.sh

set -e

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

echo "=========================================="
echo "  Starting DeerFlow in Daemon Mode"
echo "=========================================="
echo ""

./scripts/stop-services.sh

if [ ! -x "$BACKEND_RUNNER" ]; then
    echo "✗ Backend runner not found: $BACKEND_RUNNER"
    exit 1
fi

# Start backend (LangGraph API)
echo "Starting backend (LangGraph API)..."
cd "$REPO_ROOT/backend"
setsid bash -lc "cd '$REPO_ROOT/backend' && exec '$BACKEND_RUNNER' langgraph dev --host 0.0.0.0 --port $LANGGRAPH_PORT --no-browser --allow-blocking" \
    > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_DIR/backend.pid"
echo "  Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 3

# Start Gateway
echo "Starting Gateway..."
cd "$REPO_ROOT/backend"
setsid bash -lc "cd '$REPO_ROOT/backend' && exec '$BACKEND_RUNNER' uvicorn app.gateway.app:app --host 0.0.0.0 --port $GATEWAY_PORT" \
    > "$LOG_DIR/gateway.log" 2>&1 < /dev/null &
GATEWAY_PID=$!
echo $GATEWAY_PID > "$PID_DIR/gateway.pid"
echo "  Gateway PID: $GATEWAY_PID"

# Start frontend
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

# Start nginx
echo "Starting nginx..."
nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT"
echo "  nginx started"

# Wait for services to be ready
echo ""
echo "Waiting for services to be ready..."
sleep 5

# Check status
echo ""
echo "=========================================="
echo "  DeerFlow Daemon Status"
echo "=========================================="
echo ""

if ps -p $BACKEND_PID > /dev/null; then
    echo "✅ Backend:     Running (PID: $BACKEND_PID)"
else
    echo "❌ Backend:     Failed"
fi

if ps -p $GATEWAY_PID > /dev/null; then
    echo "✅ Gateway:     Running (PID: $GATEWAY_PID)"
else
    echo "❌ Gateway:     Failed"
fi

if ./scripts/wait-for-http.sh "http://127.0.0.1:${LANGGRAPH_PORT}/docs" 30 "LangGraph" >/dev/null 2>&1; then
    echo "✅ LangGraph:   Healthy (port $LANGGRAPH_PORT)"
else
    echo "❌ LangGraph:   Failed health check"
fi

if ./scripts/wait-for-http.sh "http://127.0.0.1:${GATEWAY_PORT}/health" 30 "Gateway" >/dev/null 2>&1; then
    echo "✅ Gateway API: Healthy (port $GATEWAY_PORT)"
else
    echo "❌ Gateway API: Failed health check"
fi

if ./scripts/wait-for-http.sh "http://127.0.0.1:${FRONTEND_PORT}/" 30 "Frontend" >/dev/null 2>&1; then
    echo "✅ Frontend:    Healthy (port $FRONTEND_PORT)"
else
    echo "❌ Frontend:    Failed"
fi

if ./scripts/wait-for-http.sh "http://127.0.0.1:${NGINX_PORT}/health" 15 "Nginx" >/dev/null 2>&1; then
    echo "✅ nginx:       Healthy (port $NGINX_PORT)"
else
    echo "❌ nginx:       Failed"
fi

echo ""
echo "Access URL: http://localhost:${NGINX_PORT}"
echo ""
echo "Logs: $LOG_DIR/"
echo "PIDs: $PID_DIR/"
echo ""
echo "To stop: make stop"
echo "=========================================="
