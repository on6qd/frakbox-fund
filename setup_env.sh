#!/usr/bin/env bash
# Fresh-clone environment setup for the frakbox_fund research system.
#
# WHY THIS EXISTS: `pip install -r requirements.txt` fails with
# ResolutionImpossible on a fresh clone. alpaca-trade-api==3.2.0 hard-pins
# PyYAML==6.0.1 and websockets<11, but requirements.txt pins PyYAML==6.0.3 and
# yfinance==1.2.0 needs websockets>=13 (it imports websockets.asyncio). The
# resolver cannot satisfy both at once.
#
# WORKAROUND (verified working): install alpaca first (lets it pin PyYAML
# 6.0.1), then install the rest, then force websockets to 13.1 which satisfies
# yfinance. alpaca only loses its streaming client (REST trading is unaffected).
set -e

python3 -m venv venv
. venv/bin/activate
pip install -q --upgrade pip

# 1. alpaca first — it pins PyYAML==6.0.1 and websockets<11
pip install -q "alpaca-trade-api==3.2.0"

# 2. the rest of the stack (keep PyYAML 6.0.1 that alpaca chose)
pip install -q \
  beautifulsoup4==4.14.3 \
  "numpy==2.4.3" \
  scipy==1.17.1 \
  requests==2.32.5 \
  xlrd \
  "yfinance==1.2.0" \
  "statsmodels>=0.14.0" \
  libsql-experimental \
  lxml \
  pyarrow

# 3. yfinance needs websockets.asyncio (>=13); alpaca's <11 pin breaks it.
#    13.1 satisfies yfinance; only alpaca's (unused) websocket streaming is affected.
pip install -q "websockets==13.1"

python3 -c "import numpy, pandas, scipy, statsmodels, yfinance, libsql_experimental; print('env OK: imports succeed')"
echo "Setup complete. Activate with: . venv/bin/activate"
