#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -f "backend/venv/bin/activate" ]; then
    source backend/venv/bin/activate
elif [ -f "backend/.venv/bin/activate" ]; then
    source backend/.venv/bin/activate
fi

echo "Starting Patchwork Container Pool Daemon..."
exec python backend/tasks/pool/pool_daemon.py
