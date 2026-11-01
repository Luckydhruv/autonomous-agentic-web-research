#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
CONTAINER="autonomous-research-crawl4ai"
IMAGE="unclecode/crawl4ai:0.9.2"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the selected local Crawl4AI API. Install Docker, then run this script again." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing .env. Copy .env.example to .env first." >&2
  exit 1
fi

CRAWL4AI_API_TOKEN="$(
  python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("CRAWL4AI_API_TOKEN="):
        print(line.split("=", 1)[1].strip())
        break
PY
)"

if [[ -z "$CRAWL4AI_API_TOKEN" || "$CRAWL4AI_API_TOKEN" == "generate_a_long_random_token" ]]; then
  CRAWL4AI_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  if rg -q '^CRAWL4AI_API_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^CRAWL4AI_API_TOKEN=.*|CRAWL4AI_API_TOKEN=$CRAWL4AI_API_TOKEN|" "$ENV_FILE"
  else
    printf '\nCRAWL4AI_API_TOKEN=%s\n' "$CRAWL4AI_API_TOKEN" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker start "$CONTAINER" >/dev/null
else
  docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --shm-size=1g \
    -p 11235:11235 \
    -e CRAWL4AI_API_TOKEN="$CRAWL4AI_API_TOKEN" \
    "$IMAGE" >/dev/null
fi

for _ in {1..20}; do
  if curl -fsS http://localhost:11235/health >/dev/null 2>&1; then
    echo "Crawl4AI is ready at http://localhost:11235."
    exit 0
  fi
  sleep 2
done

echo "Crawl4AI container started but did not become healthy. Check: docker logs $CONTAINER" >&2
exit 1
