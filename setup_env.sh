#!/usr/bin/env bash
# Environment setup for frakbox_fund on a fresh clone.
#
# WHY THIS SCRIPT EXISTS:
#   `pip install -r requirements.txt` FAILS with ResolutionImpossible on a clean
#   environment because of two hard, irreconcilable pins:
#     - alpaca-trade-api==3.2.0  requires  PyYAML==6.0.1  and  websockets<11
#     - yfinance==1.2.0          requires  websockets>=13
#   These ranges do not overlap, so pip's resolver cannot satisfy both.
#
#   We only use alpaca for its REST API (account, orders, positions — see
#   trader.py), never its websocket streaming, so the websockets version it
#   pins is irrelevant at runtime. yfinance, by contrast, genuinely needs
#   websockets>=13. The fix: install everything EXCEPT alpaca normally (clean
#   resolve, gets websockets>=13 for yfinance), then install alpaca with
#   --no-deps and add back only its runtime deps that don't conflict.
#
# USAGE:
#   bash setup_env.sh
#   source venv/bin/activate
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate

python3 -m pip install -q --upgrade pip

# 1. Install everything except alpaca-trade-api (resolves cleanly; yfinance pulls websockets>=13)
grep -v '^alpaca-trade-api' requirements.txt > /tmp/req_noalpaca.txt
pip install -q -r /tmp/req_noalpaca.txt

# 2. Install alpaca without its (conflicting) dependency pins — REST-only usage
pip install -q --no-deps alpaca-trade-api==3.2.0

# 3. Add back alpaca's runtime deps that do NOT conflict with the above
pip install -q aiohttp "websocket-client>=1.0" deprecation msgpack

# 4. Sanity check: the modules the research stack actually imports
python3 - <<'PY'
import importlib
for m in ("yfinance", "pandas", "numpy", "scipy", "statsmodels.api",
          "alpaca_trade_api", "libsql_experimental"):
    importlib.import_module(m)
print("setup_env.sh: all core imports OK")
PY

echo "Done. Activate with: source venv/bin/activate"
