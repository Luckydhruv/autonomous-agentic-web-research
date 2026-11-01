#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
PID_FILE="$ROOT/.crawl4ai.pid"
LOG_FILE="$ROOT/.crawl4ai.log"
PYTHON="$ROOT/venv/bin/python"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing .env. Copy .env.example to .env first." >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" ]] || ! CRAWL4_AI_BASE_DIRECTORY="$ROOT" "$PYTHON" -c 'import crawl4ai, uvicorn' >/dev/null 2>&1; then
  echo "Crawl4AI is not installed. Run: $PYTHON -m pip install -r backend/requirements.txt" >&2
  exit 1
fi

TOKEN="$("$PYTHON" - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("CRAWL4AI_API_TOKEN="):
        print(line.split("=", 1)[1].strip())
        break
PY
)"
if [[ -z "$TOKEN" || "$TOKEN" == "generate_a_long_random_token" ]]; then
  TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  if rg -q '^CRAWL4AI_API_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^CRAWL4AI_API_TOKEN=.*|CRAWL4AI_API_TOKEN=$TOKEN|" "$ENV_FILE"
  else
    printf '\nCRAWL4AI_API_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
fi

if [[ "${1:-}" == "--foreground" ]]; then
  cd "$ROOT/backend"
  export CRAWL4AI_API_TOKEN="$TOKEN"
  export CRAWL4_AI_BASE_DIRECTORY="$ROOT"
  exec "$PYTHON" -m uvicorn crawl4ai_server:app --host 127.0.0.1 --port 11235
fi

if curl -fsS http://127.0.0.1:11235/health >/dev/null 2>&1; then
  echo "Local Crawl4AI API is already running at http://127.0.0.1:11235."
  exit 0
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Crawl4AI process is running but not healthy. See $LOG_FILE" >&2
  exit 1
fi

(cd "$ROOT/backend" && CRAWL4AI_API_TOKEN="$TOKEN" CRAWL4_AI_BASE_DIRECTORY="$ROOT" nohup "$PYTHON" -m uvicorn crawl4ai_server:app --host 127.0.0.1 --port 11235 >> "$LOG_FILE" 2>&1 & echo $! > "$PID_FILE")

for _ in {1..45}; do
  if curl -fsS http://127.0.0.1:11235/health >/dev/null 2>&1; then
    echo "Local Crawl4AI API is ready at http://127.0.0.1:11235."
    exit 0
  fi
  sleep 2
done

echo "Crawl4AI did not become healthy. See $LOG_FILE" >&2
exit 1
