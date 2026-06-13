#!/usr/bin/env bash
# Reproducible environment setup for the frakbox_fund research system.
#
# Why this script exists: `pip install -r requirements.txt` alone cannot install
# the Alpaca trading SDK, because alpaca-trade-api==3.2.0 pins websockets<11 while
# yfinance==1.2.0 needs websockets>=13 — a hard, unresolvable conflict. Only the
# REST client is used (trader.py: tradeapi.REST), so we install the research stack
# first, then add alpaca with --no-deps (its REST-only deps are already in
# requirements.txt). This keeps a fresh-env install deterministic.
set -e

python3 -m venv venv
. venv/bin/activate
pip install -q -r requirements.txt
# alpaca for the trade loop (REST only); --no-deps avoids the websockets<11 pin.
pip install -q --no-deps alpaca-trade-api==3.2.0
echo "Environment ready. Activate with: . venv/bin/activate"
