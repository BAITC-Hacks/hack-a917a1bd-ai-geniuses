#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

