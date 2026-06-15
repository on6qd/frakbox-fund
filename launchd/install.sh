#!/bin/bash
# Install / reload the frakbox_fund launchd jobs (local execution layer).
#
#   trade loop  — every 120s: stop-losses, triggers, reconciliation (RunAtLoad)
#   scanners    — daily: 52w-low, S&P additions, CEO departures, insider clusters
#
# All jobs read .env for Turso + Alpaca + data creds (no secrets in the plists).
# Usage:  ./launchd/install.sh           (install + load all)
#         ./launchd/install.sh uninstall (unload + remove all)
set -euo pipefail

REPO="/Users/frakbox/Bots/frakbox_fund"
DEST="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
LABELS=(com.frakbox.tradeloop com.frakbox.scanner-52wlow com.frakbox.scanner-sp500 com.frakbox.scanner-ceo com.frakbox.scanner-cluster)

mkdir -p "$DEST" "$REPO/logs"

if [ "${1:-install}" = "uninstall" ]; then
    for L in "${LABELS[@]}"; do
        launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true
        rm -f "$DEST/$L.plist"
        echo "removed $L"
    done
    exit 0
fi

for L in "${LABELS[@]}"; do
    cp "$REPO/launchd/$L.plist" "$DEST/$L.plist"
    launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true   # idempotent reload
    launchctl bootstrap "gui/$UID_N" "$DEST/$L.plist"
    echo "loaded $L"
done
echo "Done. Check:  launchctl list | grep frakbox"
