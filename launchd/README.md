# launchd jobs — local execution layer

Version-controlled launchd plists for the **local** side of the system (the
research sessions run in the cloud — see `CLOUD_ROUTINE.md`). Keeping them in the
repo (vs. only in `~/Library/LaunchAgents`) means the setup survives renames and
is reproducible.

| Job | Script | Schedule | RunAtLoad |
|---|---|---|---|
| `com.frakbox.tradeloop` | `trade_loop.py` | every 120s | yes |
| `com.frakbox.scanner-52wlow` | `tools/fiftytwo_week_low_scanner.py` | daily 22:00 local | no |
| `com.frakbox.scanner-sp500` | `tools/sp500_addition_scanner.py` | daily 22:10 local | no |
| `com.frakbox.scanner-ceo` | `tools/ceo_departure_daily_scan.py` | daily 22:20 local | no |
| `com.frakbox.scanner-cluster` | `tools/cluster_auto_scanner.py` | daily 22:30 local | no |

The scanner times are sensible placeholders — edit the `StartCalendarInterval`
in each plist to taste, then re-run the installer.

## Secrets
None are in the plists. Each process reads `.env` itself (config.py + db.py both
auto-load it), so Turso/Alpaca/data credentials never sit in `~/Library` plists.

## Install / reload
```bash
./launchd/install.sh             # copy to ~/Library/LaunchAgents + (re)load all
launchctl list | grep frakbox    # verify
./launchd/install.sh uninstall   # unload + remove all
```

Loading `com.frakbox.tradeloop` resumes **live local paper trading** immediately
(RunAtLoad + 120s). The scanners only fire at their scheduled time.
