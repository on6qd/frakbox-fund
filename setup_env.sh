#!/usr/bin/env bash
# Fresh-clone environment setup for the market causal-research system.
#
# WHY THIS EXISTS: a bare `pip install -r requirements.txt` fails with
# ResolutionImpossible on a fresh clone. alpaca-trade-api 3.2.0 pins
# websockets<11 (and PyYAML==6.0.1), but yfinance>=1.x needs websockets>=13
# (it imports websockets.sync). The two cannot be co-resolved by the strict
# resolver. alpaca's REST trading API does not need the websockets streaming
# client, so we install everything else first and then force websockets>=13.
# yfinance (all data fetching) then works; alpaca REST is unaffected.
#
# Usage:
#   ./setup_env.sh && . venv/bin/activate
set -euo pipefail

python3 -m venv venv
# shellcheck disable=SC1091
. venv/bin/activate

python3 -m pip install -q --upgrade pip

# Install everything EXCEPT the strict alpaca pin's transitive websockets clash.
# Use the legacy resolver so the websockets override below is not fought.
pip install -q --use-deprecated=legacy-resolver -r requirements.txt || true

# Force a websockets version yfinance can import (websockets.sync, added in 13).
pip install -q "websockets>=13"

# Verify the imports that actually matter for research.
python3 - <<'PY'
import pandas, numpy, scipy, statsmodels, yfinance, libsql_experimental
from tools.yfinance_utils import get_close_prices  # noqa
import db  # Turso auto-detect via TURSO_* env vars
print("env OK: core imports + db module load succeeded")
PY

echo
echo "Setup complete. Activate with:  . venv/bin/activate"
echo "If TURSO_DATABASE_URL / TURSO_AUTH_TOKEN are unset, db.py will fall back"
echo "to a LOCAL empty sqlite db — do not run research against an empty db."
