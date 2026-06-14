#!/usr/bin/env bash
#
# setup_env.sh — reproducible Python environment for frakbox_fund.
#
# Why this script exists (do NOT just run `pip install -r requirements.txt`):
#   requirements.txt has two unavoidable transitive conflicts that the modern
#   pip resolver refuses to install (ResolutionImpossible):
#     1. alpaca-trade-api==3.2.0 hard-pins PyYAML==6.0.1, but we pin 6.0.3.
#     2. alpaca-trade-api==3.2.0 hard-pins websockets<11, but yfinance==1.2.0
#        needs websockets>=13.0 (it imports `websockets.sync` at module load).
#
#   The websockets clash is the dangerous one: if alpaca's websockets 10.4 wins,
#   `import yfinance` crashes with `ModuleNotFoundError: No module named
#   'websockets.sync'` — and yfinance is our core price source.
#
# Resolution (verified working 2026-06-14):
#   - Install everything with the legacy resolver (installs despite the warnings).
#   - Then force websockets>=13.0 so yfinance imports. We only use alpaca's REST
#     client (paper trades), which works fine on websockets 16.x; only alpaca's
#     live-streaming socket — which we never use — cares about the old pin.
#   - The residual PyYAML 6.0.3 warning is harmless (alpaca's yaml use is config-only).
#
# Idempotent: safe to re-run. Creates ./venv if missing.
#
# Usage:
#   bash setup_env.sh          # build/repair the env
#   source venv/bin/activate   # then activate it for your shell
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup_env] creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[setup_env] upgrading pip"
pip install -q --upgrade pip

echo "[setup_env] installing requirements (legacy resolver — tolerates known pin conflicts)"
pip install -q --use-deprecated=legacy-resolver -r "$REPO_DIR/requirements.txt"

echo "[setup_env] forcing websockets>=13.0 so yfinance can import (overrides alpaca's stale pin)"
pip install -q "websockets>=13.0"

echo "[setup_env] verifying the stack imports"
python3 - <<'PY'
import yfinance, alpaca_trade_api
from alpaca_trade_api.rest import REST          # the only alpaca client we use
import statsmodels.api, scipy, bs4, pandas, numpy, yaml, websockets, libsql_experimental
print(f"[setup_env] OK — websockets {websockets.__version__}, PyYAML {yaml.__version__}, "
      f"pandas {pandas.__version__}, numpy {numpy.__version__}")
PY

echo "[setup_env] done. Activate with:  source venv/bin/activate"
