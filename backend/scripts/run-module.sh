#!/usr/bin/env bash

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PYTHON="${DEER_FLOW_BACKEND_PYTHON:-$BACKEND_DIR/.venv/bin/python}"

if [ ! -x "$BACKEND_PYTHON" ]; then
    echo "Backend Python not found: $BACKEND_PYTHON" >&2
    echo "Run 'cd $BACKEND_DIR && uv sync' to create the virtual environment." >&2
    exit 1
fi

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <module> [args...]" >&2
    exit 2
fi

MODULE="$1"
shift

cd "$BACKEND_DIR"

case "$MODULE" in
    uvicorn)
        exec env PYTHONPATH=. "$BACKEND_PYTHON" -m uvicorn "$@"
        ;;
    langgraph|langgraph_cli)
        exec env PYTHONPATH=. NO_COLOR="${NO_COLOR:-1}" "$BACKEND_PYTHON" -m langgraph_cli "$@"
        ;;
    *)
        exec env PYTHONPATH=. "$BACKEND_PYTHON" -m "$MODULE" "$@"
        ;;
esac
