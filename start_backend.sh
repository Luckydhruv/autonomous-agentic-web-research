#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

if [ -f "$DIR/venv/bin/python" ]; then
    PYTHON="$DIR/venv/bin/python"
else
    PYTHON="python3"
fi

echo "Starting Autonomous Research Agent Backend on http://localhost:8000..."
cd "$DIR/backend"
exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
