#!/usr/bin/env bash

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$BACKEND_DIR/scripts/run-module.sh"
MODE="${1:-dev}"
shift || true

if [ ! -x "$RUNNER" ]; then
    echo "Backend runner not found: $RUNNER" >&2
    exit 1
fi

LANGGRAPH_CONFIG="${DEER_FLOW_LANGGRAPH_CONFIG:-$BACKEND_DIR/langgraph.json}"
LANGGRAPH_UP_READY=false
if [ -n "${LANGGRAPH_CLOUD_LICENSE_KEY:-}" ] && [ -f "$LANGGRAPH_CONFIG" ]; then
    LANGGRAPH_UP_READY=true
fi

case "$MODE" in
    dev)
        exec "$RUNNER" langgraph dev --no-browser --allow-blocking "$@"
        ;;
    prod)
        if $LANGGRAPH_UP_READY; then
            exec "$RUNNER" langgraph up --wait --no-pull --config "$LANGGRAPH_CONFIG" "$@"
        fi
        printf 'LANGGRAPH_RUNTIME_FALLBACK {"requested":"prod","selected":"dev-no-reload","reason":"missing LANGGRAPH_CLOUD_LICENSE_KEY or langgraph.json"}\n' >&2
        exec env BG_JOB_ISOLATED_LOOPS="${BG_JOB_ISOLATED_LOOPS:-true}" "$RUNNER" langgraph dev --no-browser --no-reload "$@"
        ;;
    *)
        echo "Usage: $0 <dev|prod> [args...]" >&2
        exit 2
        ;;
esac
