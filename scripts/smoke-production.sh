#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-2026}"
LANGGRAPH_URL="http://127.0.0.1:${PORT}/api/langgraph"
GATEWAY_URL="http://127.0.0.1:${PORT}"

wait_http() {
    local url="$1"
    local attempts="${2:-20}"
    local delay="${3:-2}"
    local attempt=1

    while [ "$attempt" -le "$attempts" ]; do
        if curl -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$delay"
        attempt=$((attempt + 1))
    done

    echo "smoke check failed for $url" >&2
    return 1
}

wait_http "$GATEWAY_URL/health"
wait_http "$GATEWAY_URL/ready"
docker exec deer-flow-channel-worker sh -lc "curl -fsS http://127.0.0.1:8010/health >/dev/null"

cd "$REPO_ROOT/backend"
PYTHONPATH=. .venv/bin/python - <<'PY'
from langgraph_sdk import get_sync_client

client = get_sync_client(url='http://127.0.0.1:2026/api/langgraph')
thread = client.threads.create()
thread_id = thread['thread_id']
result = client.runs.wait(
    thread_id,
    'lead_agent',
    input={'messages': [{'role': 'human', 'content': 'Reply with exactly: HELLO'}]},
    context={'thread_id': thread_id},
)
messages = result.get('messages') or []
if not messages:
    raise SystemExit('no messages returned from smoke test')
last = messages[-1]
content = last.get('content')
if isinstance(content, list):
    text = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
elif isinstance(content, str):
    text = content
else:
    text = ''
if text.strip() != 'HELLO':
    raise SystemExit(f'unexpected smoke response: {text!r}')
print('HELLO')
PY

echo "[ok] production smoke test passed"
