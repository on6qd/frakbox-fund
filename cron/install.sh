#!/bin/bash
# Install the frakbox_fund cron jobs — the local execution layer for this
# headless, non-admin Mac Mini (no Aqua GUI session, so launchd LaunchAgents
# can't load; cron runs regardless of login and needs no admin).
#
# Idempotent: strips any prior frakbox_fund crontab lines, preserves everything
# else. Every managed line contains "frakbox_fund" so re-running replaces cleanly.
#
# Usage:  ./cron/install.sh            (install / update)
#         ./cron/install.sh uninstall  (remove)
REPO="/Users/frakbox/Bots/frakbox_fund"
mkdir -p "$REPO/logs"

if [ "${1:-}" = "uninstall" ]; then
    crontab -l 2>/dev/null | grep -v "frakbox_fund" | crontab -
    echo "removed frakbox_fund cron jobs."
    exit 0
fi

( crontab -l 2>/dev/null | grep -v "frakbox_fund"; cat "$REPO/cron/frakbox.cron" ) | crontab -
echo "installed. frakbox_fund crontab lines:"
crontab -l | grep "frakbox_fund"
