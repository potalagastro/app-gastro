#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH=.
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
