#!/usr/bin/env bash
# Canonical environment bootstrap for the frakbox causal-research system.
#
# WHY THIS EXISTS: `pip install -r requirements.txt` FAILS with ResolutionImpossible
# on a fresh env. alpaca-trade-api==3.2.0 hard-pins PyYAML==6.0.1 (and websockets<11),
# which conflicts with requirements.txt's PyYAML==6.0.3 (and with yfinance's newer
# websockets). This script installs alpaca FIRST to fix its transitive pins, then
# installs the rest of requirements with the PyYAML line dropped (alpaca's 6.0.1 is
# fine for everything we use). The residual websockets version warning is benign:
# alpaca's websocket streaming is not used in the research/backtest paths.
#
# Usage:  ./setup_env.sh   (then: source venv/bin/activate)
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-venv}"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install --quiet --upgrade pip

# 1) alpaca first — pins PyYAML==6.0.1 and websockets<11
pip install --quiet "alpaca-trade-api==3.2.0"

# 2) everything else, minus the two packages that conflict with alpaca's pins:
#    PyYAML (alpaca pins ==6.0.1 vs requirements ==6.0.3) and alpaca itself (already
#    installed above; re-resolving it alongside yfinance triggers the websockets<11
#    hard conflict). yfinance pulls a newer websockets — that warning is benign.
grep -v -i -E '^(PyYAML|alpaca-trade-api)' requirements.txt > "$(pwd)/.req_filtered.txt"
pip install --quiet -r "$(pwd)/.req_filtered.txt"
rm -f "$(pwd)/.req_filtered.txt"

# 3) sanity check the imports the research paths actually need
python3 - <<'PY'
import pandas, numpy, scipy, statsmodels, yfinance, libsql_experimental, alpaca_trade_api  # noqa: F401
print("env OK — core research + trading imports load")
PY

echo "Done. Activate with: source $VENV/bin/activate"
