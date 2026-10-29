#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=.
export DATA_DIR="${DATA_DIR:-/var/data}"
mkdir -p "$DATA_DIR"
# Bootstrap (admin + catalog if present) then serve
python scripts/bootstrap.py
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
