#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR/frontend"

echo "Starting Patchwork Frontend (Vite) on http://localhost:5173..."
exec npm run dev
