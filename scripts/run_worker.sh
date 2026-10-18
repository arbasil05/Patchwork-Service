#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -f "backend/venv/bin/activate" ]; then
    source backend/venv/bin/activate
elif [ -f "backend/.venv/bin/activate" ]; then
    source backend/.venv/bin/activate
fi

echo "Starting Patchwork RQ Worker..."
exec python backend/worker/run_worker.py
