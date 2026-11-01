#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR/frontend"

echo "Starting Autonomous Research Agent Frontend on http://localhost:5173..."
exec npm run dev
