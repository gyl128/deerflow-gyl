#!/usr/bin/env bash
#
# start.sh - Start all DeerFlow services

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LANGGRAPH_PORT="${DEER_FLOW_LANGGRAPH_PORT:-2024}"
GATEWAY_PORT="${DEER_FLOW_GATEWAY_PORT:-8001}"
FRONTEND_PORT="${DEER_FLOW_FRONTEND_PORT:-3001}"
NGINX_PORT="${DEER_FLOW_NGINX_PORT:-2026}"
BACKEND_RUNNER="$REPO_ROOT/backend/scripts/run-module.sh"
LANGGRAPH_RUNNER="$REPO_ROOT/backend/scripts/run-langgraph.sh"

DEV_MODE=true
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=true ;;
        --prod) DEV_MODE=false ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 [--dev|--prod]"; exit 1 ;;
    esac
done

if $DEV_MODE; then
    FRONTEND_CMD="pnpm exec next dev --hostname 0.0.0.0 --port $FRONTEND_PORT --turbo"
    LANGGRAPH_MODE="dev"
else
    FRONTEND_CMD="pnpm exec next start --hostname 0.0.0.0 --port $FRONTEND_PORT"
    LANGGRAPH_MODE="prod"
fi

./scripts/stop-services.sh
sleep 1

echo ""
echo "=========================================="
echo "  Starting DeerFlow Development Server"
echo "=========================================="
echo ""
if $DEV_MODE; then
    echo "  Mode: DEV  (hot-reload enabled)"
else
    echo "  Mode: PROD (hot-reload disabled)"
fi
echo ""

if ! { [ -n "${DEER_FLOW_CONFIG_PATH:-}" ] && [ -f "$DEER_FLOW_CONFIG_PATH" ] || [ -f backend/config.yaml ] || [ -f config.yaml ]; }; then
    echo "? No DeerFlow config file found."
    exit 1
fi

"$REPO_ROOT/scripts/config-upgrade.sh"

cleanup() {
    trap - INT TERM
    echo ""
    echo "Shutting down services..."
    ./scripts/stop-services.sh
    exit 0
}
trap cleanup INT TERM

mkdir -p logs

if [ ! -x "$BACKEND_RUNNER" ] || [ ! -x "$LANGGRAPH_RUNNER" ]; then
    echo "? Backend runners not found"
    exit 1
fi

if ! $DEV_MODE && [ ! -f "$REPO_ROOT/frontend/.next/BUILD_ID" ]; then
    echo "Building frontend for production..."
    (cd frontend && rm -f .next/lock .next/dev/lock 2>/dev/null || true && pnpm exec next build > ../logs/frontend-build.log 2>&1)
fi

GATEWAY_EXTRA_FLAGS=""
if $DEV_MODE; then
    GATEWAY_EXTRA_FLAGS="--reload --reload-include='*.yaml' --reload-include='.env'"
fi

echo "Starting LangGraph server..."
(cd backend && ./scripts/run-langgraph.sh "$LANGGRAPH_MODE" --host 0.0.0.0 --port "$LANGGRAPH_PORT" > ../logs/langgraph.log 2>&1) &
./scripts/wait-for-port.sh "$LANGGRAPH_PORT" 90 "LangGraph" || {
    echo "  See logs/langgraph.log for details"
    tail -20 logs/langgraph.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${LANGGRAPH_PORT}/docs" 90 "LangGraph" || {
    echo "  See logs/langgraph.log for details"
    tail -20 logs/langgraph.log
    cleanup
}
echo "? LangGraph server started on localhost:${LANGGRAPH_PORT}"

echo "Starting Gateway API..."
(cd backend && "$BACKEND_RUNNER" uvicorn app.gateway.app:app --host 0.0.0.0 --port "$GATEWAY_PORT" $GATEWAY_EXTRA_FLAGS > ../logs/gateway.log 2>&1) &
./scripts/wait-for-port.sh "$GATEWAY_PORT" 60 "Gateway API" || {
    echo "? Gateway API failed to start. Last log output:"
    tail -60 logs/gateway.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${GATEWAY_PORT}/health" 60 "Gateway API" || {
    echo "? Gateway API failed health check. Last log output:"
    tail -60 logs/gateway.log
    cleanup
}
echo "? Gateway API started on localhost:${GATEWAY_PORT}"

echo "Starting Frontend..."
(cd frontend && rm -f .next/lock .next/dev/lock 2>/dev/null || true && $FRONTEND_CMD > ../logs/frontend.log 2>&1) &
./scripts/wait-for-port.sh "$FRONTEND_PORT" 120 "Frontend" || {
    echo "  See logs/frontend.log for details"
    tail -20 logs/frontend.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${FRONTEND_PORT}/" 60 "Frontend" || {
    echo "  See logs/frontend.log for details"
    tail -20 logs/frontend.log
    cleanup
}
echo "? Frontend started on localhost:${FRONTEND_PORT}"

echo "Starting Nginx reverse proxy..."
nginx -g 'daemon off;' -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" > logs/nginx.log 2>&1 &
./scripts/wait-for-port.sh "$NGINX_PORT" 15 "Nginx" || {
    echo "  See logs/nginx.log for details"
    tail -10 logs/nginx.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${NGINX_PORT}/health" 30 "Nginx" || {
    echo "  See logs/nginx.log for details"
    tail -10 logs/nginx.log
    cleanup
}
echo "? Nginx started on localhost:${NGINX_PORT}"

echo ""
echo "=========================================="
if $DEV_MODE; then
    echo "  ? DeerFlow development server is running!"
else
    echo "  ? DeerFlow production server is running!"
fi
echo "=========================================="
echo ""
echo "  ?? Application: http://localhost:${NGINX_PORT}"
echo "  ?? API Gateway: http://localhost:${NGINX_PORT}/api/*"
echo "  ?? LangGraph:   http://localhost:${NGINX_PORT}/api/langgraph/*"
echo ""
echo "Press Ctrl+C to stop all services"

wait
