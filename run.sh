#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, httpx, dotenv' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8765}"
