#!/usr/bin/env bash
# Environment setup for frakbox_fund research sessions.
#
# `pip install -r requirements.txt` FAILS with ResolutionImpossible because
# alpaca-trade-api's transitive pins (PyYAML / websockets) conflict with the
# pinned yfinance. This script installs a working set instead:
#   1. core scientific + data deps (yfinance left UNpinned so pip is free),
#   2. alpaca-trade-api unpinned (only needed for live trading; it downgrades
#      websockets, which is fine — we don't stream),
#   3. websockets>=13 LAST, because yfinance needs `websockets.sync` for
#      historical downloads. alpaca only warns about the newer websockets and
#      works fine for REST order placement.
#
# Usage:  . setup_env.sh        (or: bash setup_env.sh && . venv/bin/activate)
set -e

python3 -m venv venv
. venv/bin/activate
pip install -q --upgrade pip

# 1. Core deps (yfinance unpinned to avoid the alpaca conflict)
pip install -q libsql-experimental pandas numpy scipy statsmodels \
    requests beautifulsoup4 PyYAML urllib3 xlrd yfinance

# 2. Alpaca (unpinned; needed only by trader.py for live paper trades)
pip install -q alpaca-trade-api || echo "WARN: alpaca install failed (trading disabled this session)"

# 3. Restore a websockets new enough for yfinance.sync
pip install -q 'websockets>=13.0'

python3 -c "import db, yfinance, websockets.sync; print('env OK — db + yfinance import cleanly')"
echo "Done. Activate with: . venv/bin/activate"
