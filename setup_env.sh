#!/usr/bin/env bash
# Environment setup for frakbox_fund.
#
# WHY THIS EXISTS: `pip install -r requirements.txt` CANNOT resolve in one shot.
# alpaca-trade-api==3.2.0 pins websockets<11 (and PyYAML==6.0.1) while
# yfinance==1.2.0 pins websockets>=13. The ranges do not overlap, so the modern
# pip resolver raises ResolutionImpossible. The two packages are only soft-
# incompatible at runtime: alpaca uses websockets for its live STREAM module,
# which this project does not use (paper trades go through the REST API), so the
# fund runs fine with the newer websockets that yfinance needs.
#
# The fix is a STAGED install: install alpaca first, then everything else (which
# upgrades websockets). pip prints a harmless warning about alpaca's websockets
# pin but the import works (verified: `import alpaca_trade_api` succeeds).
#
# Usage:  bash setup_env.sh && source venv/bin/activate
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

python -m pip install -q --upgrade pip

# Stage 1: alpaca alone (brings PyYAML==6.0.1, websockets<11).
pip install -q alpaca-trade-api==3.2.0

# Stage 2: the rest. yfinance upgrades websockets past alpaca's pin; this is
# expected and safe (REST-only usage). --upgrade-strategy only-if-needed keeps
# alpaca's other deps in place.
pip install -q --upgrade-strategy only-if-needed \
  beautifulsoup4==4.14.3 \
  numpy==2.4.3 \
  pandas==3.0.1 \
  requests==2.32.5 \
  scipy==1.17.1 \
  urllib3==1.26.19 \
  'xlrd>=2.0.1' \
  yfinance==1.2.0 \
  'statsmodels>=0.14.0' \
  libsql-experimental

# Verify the stack imports (this is the real test, not pip's resolver warnings).
python - <<'PY'
import alpaca_trade_api, pandas, numpy, scipy, statsmodels, yfinance, libsql_experimental
print("setup_env.sh OK — all imports succeed (alpaca", alpaca_trade_api.__version__ + ")")
PY
