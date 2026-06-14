#!/usr/bin/env bash
# Fresh-clone environment setup for the causal-research system.
#
# Why this exists: `pip install -r requirements.txt` FAILS on a fresh clone with
# ResolutionImpossible. alpaca-trade-api==3.2.0 pins PyYAML==6.0.1 and websockets<11,
# which conflicts with the pinned PyYAML==6.0.3 (and with yfinance's websockets needs).
# We only use Alpaca's REST client (trading), which is unaffected by the websockets bump.
#
# Strategy: install alpaca first (let it pin PyYAML/websockets), then install the rest
# with relaxed numpy/pandas/yfinance pins. Verified end-to-end 2026-06-14.
#
# Usage:  ./setup_env.sh && . venv/bin/activate
set -euo pipefail

python3 -m venv venv
# shellcheck disable=SC1091
. venv/bin/activate

python3 -m pip install -q --upgrade pip

# 1) alpaca first — it pins PyYAML==6.0.1 and websockets<11 (REST-only use, fine for us).
pip install -q "alpaca-trade-api==3.2.0"

# 2) the rest, with relaxed numpy/pandas/yfinance pins to avoid the resolver deadlock.
pip install -q \
  "beautifulsoup4==4.14.3" \
  "numpy" \
  "pandas" \
  "requests==2.32.5" \
  "scipy" \
  "urllib3==1.26.19" \
  "xlrd>=2.0.1" \
  "yfinance" \
  "statsmodels>=0.14.0" \
  "libsql-experimental"

# Sanity check the imports the research code actually needs.
python3 - <<'PY'
import numpy, pandas, scipy, statsmodels, yfinance, libsql_experimental, requests, bs4, yaml
print("env OK — pandas", pandas.__version__, "numpy", numpy.__version__)
PY

echo "Done. Activate with:  . venv/bin/activate"
