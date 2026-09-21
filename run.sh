#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

exec .venv/bin/uvicorn app.server:app \
  --host "${VIDEOGEN_HOST:-127.0.0.1}" \
  --port "${VIDEOGEN_PORT:-8090}" \
  --reload
