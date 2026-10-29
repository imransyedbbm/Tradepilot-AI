#!/usr/bin/env bash
set -euo pipefail
[ -f .env ] || { cp .env.example .env; echo "Created .env — set TP_API_TOKEN before real use."; }
pip install -e ".[dev]"
pytest
exec uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000
