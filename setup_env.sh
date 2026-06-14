#!/usr/bin/env bash
#
# Fresh-clone environment setup for the causal-research system.
#
# WHY THIS SCRIPT EXISTS:
#   A single `pip install -r requirements.txt` is structurally IMPOSSIBLE and fails with
#   "ResolutionImpossible". Root cause: alpaca-trade-api==3.2.0 transitively pins
#   websockets<11, while yfinance==1.2.0 pins websockets>=13. No single version satisfies
#   both, so pip's all-at-once resolver gives up before installing anything.
#
#   We only use alpaca's REST API for paper trading (no streaming), which works fine with
#   newer websockets. So we install the packages SEQUENTIALLY — each `pip install` is its
#   own resolution, so the soft websockets conflict degrades to a harmless warning instead
#   of a hard failure. The final import check is the real gate on success.
#
# USAGE:
#   bash setup_env.sh && source venv/bin/activate
#
set -u

python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip -q

# Install each requirement on its own line so pip resolves them independently.
# Order matters: alpaca (line 1) pins websockets<11 first; yfinance (line 10) then bumps
# websockets past that pin — fine for REST-only usage.
while IFS= read -r pkg || [ -n "$pkg" ]; do
  # skip blank lines and comments
  [ -z "$pkg" ] && continue
  case "$pkg" in \#*) continue ;; esac
  echo ">>> pip install $pkg"
  pip install -q "$pkg" || echo "    (non-fatal: $pkg reported a dependency conflict; continuing)"
done < requirements.txt

echo "=== verifying environment ==="
python3 - <<'PY'
import importlib, sys
mods = ["numpy", "pandas", "scipy", "statsmodels", "yfinance",
        "libsql_experimental", "yaml", "bs4", "requests"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        missing.append(f"{m}: {e}")
if missing:
    print("ENV BROKEN — missing/failed imports:")
    for x in missing:
        print("  -", x)
    sys.exit(1)
import numpy, pandas
print(f"env OK | numpy {numpy.__version__} | pandas {pandas.__version__}")
PY
