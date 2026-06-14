#!/usr/bin/env bash
# Fresh-environment setup for the frakbox causal-research system.
#
# `pip install -r requirements.txt` FAILS (ResolutionImpossible): alpaca-trade-api
# pins PyYAML/websockets versions that conflict with yfinance. This script installs
# a working combination in the right order. Recurring friction (145x+); use this
# instead of requirements.txt for a fresh venv.
#
# Usage:  bash setup_env.sh   (creates ./venv and installs everything)
set -euo pipefail

PYTHON="${PYTHON:-python3}"

if [ ! -d venv ]; then
  "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
. venv/bin/activate

# 1) Core research + data deps (yfinance unpinned so it pulls a modern build).
pip install -q --upgrade pip
pip install -q libsql-experimental numpy pandas scipy statsmodels \
    yfinance requests beautifulsoup4 PyYAML urllib3 xlrd

# 2) Alpaca (paper trading). Unpinned — it downgrades websockets to 10.4, which
#    breaks yfinance's websockets.sync import, so we repair websockets next.
pip install -q alpaca-trade-api || echo "WARN: alpaca-trade-api install failed (trading disabled; research unaffected)"

# 3) yfinance needs websockets>=13 (websockets.sync). Alpaca only warns about this
#    at runtime and we do no live websocket trading, so the newer version is safe.
pip install -q "websockets>=13"

# 4) Sanity check: core imports + Turso connectivity.
python3 - <<'PY'
import importlib
for m in ("libsql_experimental", "pandas", "numpy", "scipy", "statsmodels", "yfinance"):
    importlib.import_module(m)
print("core imports OK")
import os
if os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"):
    import db
    n = db.get_db().execute("SELECT count(*) FROM hypotheses").fetchone()[0]
    print(f"Turso OK: {n} hypotheses")
else:
    print("WARN: TURSO_* env vars not set — db.py will fall back to local sqlite")
PY

echo "Environment ready. Activate with: . venv/bin/activate"
