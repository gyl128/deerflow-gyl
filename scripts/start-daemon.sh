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

echo "=========================================="
echo "  Starting DeerFlow in Daemon Mode"
echo "=========================================="
echo ""

# Stop existing services
echo "Stopping existing services..."
pkill -f "langgraph dev" 2>/dev/null || true
pkill -f "uvicorn app.gateway.app:app" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "next build" 2>/dev/null || true
pkill -f "next start" 2>/dev/null || true
pkill -f "next-server" 2>/dev/null || true
pkill -f "pnpm exec next" 2>/dev/null || true
pkill -f "corepack pnpm" 2>/dev/null || true
nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
sleep 1
pkill -9 nginx 2>/dev/null || true
./scripts/cleanup-containers.sh deer-flow-sandbox 2>/dev/null || true

# Start backend (LangGraph API)
echo "Starting backend (LangGraph API)..."
cd "$REPO_ROOT/backend"
nohup uv run langgraph dev --host 0.0.0.0 --port 2024 \
    > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_DIR/backend.pid"
echo "  Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 3

# Start Gateway
echo "Starting Gateway..."
cd "$REPO_ROOT/backend"
nohup uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 \
    > "$LOG_DIR/gateway.log" 2>&1 &
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
setsid bash -lc "cd '$REPO_ROOT/frontend' && rm -f .next/lock .next/dev/lock 2>/dev/null || true && exec $FRONTEND_PM exec next start --hostname 0.0.0.0 --port 3001" \
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

if ss -ltn "( sport = :3001 )" | grep -q ":3001"; then
    echo "✅ Frontend:    Running (port 3001)"
else
    echo "❌ Frontend:    Failed"
fi

if pgrep -x nginx > /dev/null; then
    echo "✅ nginx:       Running"
else
    echo "❌ nginx:       Failed"
fi

echo ""
echo "Access URL: http://localhost:2026"
echo ""
echo "Logs: $LOG_DIR/"
echo "PIDs: $PID_DIR/"
echo ""
echo "To stop: make stop  or  ./scripts/stop-daemon.sh"
echo "=========================================="
