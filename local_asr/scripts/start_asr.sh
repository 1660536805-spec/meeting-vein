#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ "${LOCAL_ASR_HOST:-127.0.0.1}" != "127.0.0.1" ]]; then
  echo "LOCAL_ASR_HOST must remain 127.0.0.1; refusing to expose the ASR service." >&2
  exit 2
fi

export LOCAL_ASR_HOST=127.0.0.1
exec uv run --python 3.12 --extra asr --extra dev uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 9000
