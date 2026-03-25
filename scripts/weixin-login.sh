#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT_DIR/backend/scripts/run-module.sh" app.channels.weixin_login "$@"

if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' | grep -qx 'deer-flow-channel-worker'; then
        echo "Restarting deer-flow-channel-worker so Weixin login takes effect..."
        docker restart deer-flow-channel-worker >/dev/null
        echo "deer-flow-channel-worker restarted."
    fi
fi
