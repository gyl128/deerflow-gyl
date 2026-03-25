#!/usr/bin/env bash
#
# start.sh - Start all DeerFlow development services
#
# Must be run from the repo root directory.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LANGGRAPH_PORT="${DEER_FLOW_LANGGRAPH_PORT:-2024}"
GATEWAY_PORT="${DEER_FLOW_GATEWAY_PORT:-8001}"
FRONTEND_PORT="${DEER_FLOW_FRONTEND_PORT:-3001}"
NGINX_PORT="${DEER_FLOW_NGINX_PORT:-2026}"
BACKEND_RUNNER="$REPO_ROOT/backend/scripts/run-module.sh"

# ── Argument parsing ─────────────────────────────────────────────────────────

DEV_MODE=true
for arg in "$@"; do
    case "$arg" in
        --dev)  DEV_MODE=true ;;
        --prod) DEV_MODE=false ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 [--dev|--prod]"; exit 1 ;;
    esac
done

if $DEV_MODE; then
    FRONTEND_CMD="pnpm exec next dev --hostname 0.0.0.0 --port $FRONTEND_PORT --turbo"
else
    FRONTEND_CMD="pnpm exec next start --hostname 0.0.0.0 --port $FRONTEND_PORT"
fi

# ── Stop existing services ────────────────────────────────────────────────────

./scripts/stop-services.sh
sleep 1

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Starting DeerFlow Development Server"
echo "=========================================="
echo ""
if $DEV_MODE; then
    echo "  Mode: DEV  (hot-reload enabled)"
    echo "  Tip:  run \`make start\` in production mode"
else
    echo "  Mode: PROD (hot-reload disabled)"
    echo "  Tip:  run \`make dev\` to start in development mode"
fi
echo ""
echo "Services starting up..."
echo "  → Backend: LangGraph + Gateway"
echo "  → Frontend: Next.js"
echo "  → Nginx: Reverse Proxy"
echo ""

# ── Config check ─────────────────────────────────────────────────────────────

if ! { \
        [ -n "$DEER_FLOW_CONFIG_PATH" ] && [ -f "$DEER_FLOW_CONFIG_PATH" ] || \
        [ -f backend/config.yaml ] || \
        [ -f config.yaml ]; \
    }; then
    echo "✗ No DeerFlow config file found."
    echo "  Checked these locations:"
    echo "    - $DEER_FLOW_CONFIG_PATH (when DEER_FLOW_CONFIG_PATH is set)"
    echo "    - backend/config.yaml"
    echo "    - ./config.yaml"
    echo ""
    echo "  Run 'make config' from the repo root to generate ./config.yaml, then set required model API keys in .env or your config file."
    exit 1
fi

# ── Auto-upgrade config ──────────────────────────────────────────────────

"$REPO_ROOT/scripts/config-upgrade.sh"

# ── Cleanup trap ─────────────────────────────────────────────────────────────

cleanup() {
    trap - INT TERM
    echo ""
    echo "Shutting down services..."
    ./scripts/stop-services.sh
    exit 0
}
trap cleanup INT TERM

# ── Start services ────────────────────────────────────────────────────────────

mkdir -p logs

if [ ! -x "$BACKEND_RUNNER" ]; then
    echo "✗ Backend runner not found: $BACKEND_RUNNER"
    exit 1
fi

if $DEV_MODE; then
    LANGGRAPH_EXTRA_FLAGS=""
    GATEWAY_EXTRA_FLAGS="--reload --reload-include='*.yaml' --reload-include='.env'"
else
    LANGGRAPH_EXTRA_FLAGS="--no-reload"
    GATEWAY_EXTRA_FLAGS=""
    if [ ! -f "$REPO_ROOT/frontend/.next/BUILD_ID" ]; then
        echo "Building frontend for production..."
        (cd frontend && rm -f .next/lock .next/dev/lock 2>/dev/null || true && pnpm exec next build > ../logs/frontend-build.log 2>&1)
    fi
fi

echo "Starting LangGraph server..."
(cd backend && "$BACKEND_RUNNER" langgraph dev --no-browser --allow-blocking --host 0.0.0.0 --port "$LANGGRAPH_PORT" $LANGGRAPH_EXTRA_FLAGS > ../logs/langgraph.log 2>&1) &
./scripts/wait-for-port.sh "$LANGGRAPH_PORT" 60 "LangGraph" || {
    echo "  See logs/langgraph.log for details"
    tail -20 logs/langgraph.log
    if grep -qE "config_version|outdated|Environment variable .* not found|KeyError|ValidationError|config\.yaml" logs/langgraph.log 2>/dev/null; then
        echo ""
        echo "  Hint: This may be a configuration issue. Try running 'make config-upgrade' to update your config.yaml."
    fi
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${LANGGRAPH_PORT}/docs" 30 "LangGraph" || {
    echo "  See logs/langgraph.log for details"
    tail -20 logs/langgraph.log
    cleanup
}
echo "✓ LangGraph server started on localhost:${LANGGRAPH_PORT}"

echo "Starting Gateway API..."
(cd backend && "$BACKEND_RUNNER" uvicorn app.gateway.app:app --host 0.0.0.0 --port "$GATEWAY_PORT" $GATEWAY_EXTRA_FLAGS > ../logs/gateway.log 2>&1) &
./scripts/wait-for-port.sh "$GATEWAY_PORT" 30 "Gateway API" || {
    echo "✗ Gateway API failed to start. Last log output:"
    tail -60 logs/gateway.log
    echo ""
    echo "Likely configuration errors:"
    grep -E "Failed to load configuration|Environment variable .* not found|config\.yaml.*not found" logs/gateway.log | tail -5 || true
    echo ""
    echo "  Hint: Try running 'make config-upgrade' to update your config.yaml with the latest fields."
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${GATEWAY_PORT}/health" 30 "Gateway API" || {
    echo "✗ Gateway API failed health check. Last log output:"
    tail -60 logs/gateway.log
    cleanup
}
echo "✓ Gateway API started on localhost:${GATEWAY_PORT}"

echo "Starting Frontend..."
(cd frontend && rm -f .next/lock .next/dev/lock 2>/dev/null || true && $FRONTEND_CMD > ../logs/frontend.log 2>&1) &
./scripts/wait-for-port.sh "$FRONTEND_PORT" 120 "Frontend" || {
    echo "  See logs/frontend.log for details"
    tail -20 logs/frontend.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${FRONTEND_PORT}/" 30 "Frontend" || {
    echo "  See logs/frontend.log for details"
    tail -20 logs/frontend.log
    cleanup
}
echo "✓ Frontend started on localhost:${FRONTEND_PORT}"

echo "Starting Nginx reverse proxy..."
nginx -g 'daemon off;' -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" > logs/nginx.log 2>&1 &
NGINX_PID=$!
./scripts/wait-for-port.sh "$NGINX_PORT" 10 "Nginx" || {
    echo "  See logs/nginx.log for details"
    tail -10 logs/nginx.log
    cleanup
}
./scripts/wait-for-http.sh "http://127.0.0.1:${NGINX_PORT}/health" 15 "Nginx" || {
    echo "  See logs/nginx.log for details"
    tail -10 logs/nginx.log
    cleanup
}
echo "✓ Nginx started on localhost:${NGINX_PORT}"

# ── Ready ─────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
if $DEV_MODE; then
    echo "  ✓ DeerFlow development server is running!"
else
    echo "  ✓ DeerFlow production server is running!"
fi
echo "=========================================="
echo ""
echo "  🌐 Application: http://localhost:${NGINX_PORT}"
echo "  📡 API Gateway: http://localhost:${NGINX_PORT}/api/*"
echo "  🤖 LangGraph:   http://localhost:${NGINX_PORT}/api/langgraph/*"
echo ""
echo "  📋 Logs:"
echo "     - LangGraph: logs/langgraph.log"
echo "     - Gateway:   logs/gateway.log"
echo "     - Frontend:  logs/frontend.log"
echo "     - Nginx:     logs/nginx.log"
echo ""
echo "Press Ctrl+C to stop all services"

wait
