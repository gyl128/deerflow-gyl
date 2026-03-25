#!/usr/bin/env bash

set -euo pipefail

URL="${1:?Usage: wait-for-http.sh <url> [timeout_seconds] [service_name]}"
TIMEOUT="${2:-60}"
SERVICE="${3:-Service}"

elapsed=0
interval=1

check_http() {
    if command -v curl >/dev/null 2>&1; then
        if curl -fsS --max-time 2 "$URL" >/dev/null 2>&1; then
            return 0
        fi
    fi

    if python3 - "$URL" <<'PY' >/dev/null 2>&1
import sys
from urllib.request import urlopen

try:
    with urlopen(sys.argv[1], timeout=2):
        pass
except Exception:
    raise SystemExit(1)
PY
    then
        return 0
    fi

    return 1
}

while ! check_http; do
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
        echo ""
        echo "✗ $SERVICE failed health check at $URL after ${TIMEOUT}s"
        exit 1
    fi
    printf "\r  Waiting for %s health check... %ds" "$SERVICE" "$elapsed"
    sleep "$interval"
    elapsed=$((elapsed + interval))
done

printf "\r  %-60s\r" ""
