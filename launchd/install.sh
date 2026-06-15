#!/bin/bash
# Install / reload the frakbox_fund launchd jobs (local execution layer).
#
#   trade loop  — every 120s: stop-losses, triggers, reconciliation (RunAtLoad)
#   scanners    — daily: 52w-low, S&P additions, CEO departures, insider clusters
#
# All jobs read .env for Turso + Alpaca + data creds (no secrets in the plists).
# Uses the per-user launchd domain (user/$UID), which is available even when the
# session has no Aqua GUI context (this Mini reports managername=Background).
# Per-user LaunchAgents need no admin/sudo.
#
# Usage:  ./launchd/install.sh           (install + load all)
#         ./launchd/install.sh uninstall (unload + remove all)

REPO="/Users/frakbox/Bots/frakbox_fund"
DEST="$HOME/Library/LaunchAgents"
DOMAIN="user/$(id -u)"
LABELS=(com.frakbox.tradeloop com.frakbox.scanner-52wlow com.frakbox.scanner-sp500 com.frakbox.scanner-ceo com.frakbox.scanner-cluster)

mkdir -p "$DEST" "$REPO/logs"

if [ "${1:-install}" = "uninstall" ]; then
    for L in "${LABELS[@]}"; do
        launchctl bootout "$DOMAIN/$L" 2>/dev/null || true
        rm -f "$DEST/$L.plist"
        echo "removed $L"
    done
    exit 0
fi

fails=0
for L in "${LABELS[@]}"; do
    cp "$REPO/launchd/$L.plist" "$DEST/$L.plist"
    launchctl bootout "$DOMAIN/$L" 2>/dev/null || true        # idempotent reload
    launchctl enable "$DOMAIN/$L" 2>/dev/null || true         # clear any disabled override
    if launchctl bootstrap "$DOMAIN" "$DEST/$L.plist" 2>/tmp/lc_err; then
        echo "loaded $L"
    else
        echo "FAILED $L: $(cat /tmp/lc_err)"; fails=$((fails+1))
    fi
done
echo "---"
[ "$fails" -eq 0 ] && echo "All loaded. Verify:  launchctl list | grep frakbox" \
                   || echo "$fails job(s) failed (error shown above)."
