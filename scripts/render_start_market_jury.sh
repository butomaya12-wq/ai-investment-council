#!/usr/bin/env bash
set -euo pipefail

: "${ALPACA_API_KEY_ID:?ALPACA_API_KEY_ID is required}"
: "${ALPACA_API_SECRET_KEY:?ALPACA_API_SECRET_KEY is required}"

mkdir -p "$HOME/.config/market-jury"
python - <<'PY'
import json
import os
from pathlib import Path

path = Path.home() / ".config" / "market-jury" / "alpaca-paper-credentials.json"
payload = {
    "provider": "ALPACA",
    "environment": "PAPER",
    "key_id": os.environ["ALPACA_API_KEY_ID"],
    "secret_key": os.environ["ALPACA_API_SECRET_KEY"],
}
path.write_text(json.dumps(payload), encoding="utf-8")
path.chmod(0o600)
print(f"Prepared Alpaca PAPER credential file at {path}")
PY

export PYTHONPATH="${PYTHONPATH:-}:src"
exec .venv/bin/uvicorn aic.cockpit_v6.app:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}"
