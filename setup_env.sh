#!/usr/bin/env bash
#
# Fresh-environment setup for frakbox_fund.
#
# WHY THIS SCRIPT EXISTS (do not replace with a plain `pip install -r requirements.txt`):
#   alpaca-trade-api==3.2.0 hard-pins  websockets<11
#   yfinance>=1.x            imports   websockets.sync  (needs websockets>=11; we use >=13)
#   No single websockets version satisfies both pins, so pip's modern resolver raises
#   ResolutionImpossible on a clean clone. (alpaca-trade-api 3.2.0 is the last release of
#   that package, so the <11 pin will never be relaxed upstream.)
#
#   Resolution: install everything with the legacy resolver (which tolerates the conflict
#   and leaves websockets at alpaca's 10.4), then force websockets>=13 so yfinance can
#   import. alpaca's REST client — the only part we use — works fine with newer websockets;
#   only its unused streaming path cares about the <11 pin.
#
# Usage:  ./setup_env.sh   (then:  . venv/bin/activate)
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv venv
# shellcheck disable=SC1091
. venv/bin/activate

pip install -q --upgrade pip
pip install -q --use-deprecated=legacy-resolver -r requirements.txt
pip install -q "websockets>=13.0"

# Fail loudly if any critical import is broken (db.py auto-detects Turso from TURSO_* env).
python3 -c "import yfinance, alpaca_trade_api, pandas, numpy, scipy, statsmodels, db; print('env OK — all critical imports succeed')"

echo
echo "Environment ready. Activate it with:  . venv/bin/activate"
