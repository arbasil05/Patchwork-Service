#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -f "backend/venv/bin/activate" ]; then
    source backend/venv/bin/activate
elif [ -f "backend/.venv/bin/activate" ]; then
    source backend/.venv/bin/activate
fi

echo "Starting Patchwork FastAPI Backend on http://127.0.0.1:8000..."
exec uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
