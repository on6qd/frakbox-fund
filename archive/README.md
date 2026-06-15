# Archived: legacy tmux-daemon scripts

These shell scripts ran the research system as a single long-lived `researcher.sh`
daemon in a tmux session (trade loop + health check + research sessions + dashboard
export, all in one process). They were archived 2026-06-13 as part of the migration to:

- **Research sessions** → a scheduled **Claude Code routine** (cloud, every 2h), reading/writing shared state in **Turso (libSQL)**.
- **Trade loop + scanners** → individual **launchd** jobs on the Mac Mini (to be recreated under #14).

| Script | What it did |
|---|---|
| `researcher.sh` | the daemon — three loops (trade 2min / health 10min / research 15min) + hourly dashboard export |
| `start.sh` / `stop.sh` | launch/kill the daemon (with pgrep guard + nohup) |
| `check.sh` / `tail.sh` | status check / log tail helpers |

## Also archived in #14 (2026-06-13)

- `health_check.py` — monitored the `researcher.sh` daemon (pgrep + restart via `start.sh`). Obsolete: the daemon is gone, the launchd trade loop runs stop-losses every 120s, and cloud routines are Anthropic-managed.
- `should_run.py` — was called only by `researcher.sh` to gate session work. Orphaned.
- `tools/` — 38 one-off trade runbooks (`activate_<ticker>`, `close_<ticker>`, `monday_*`, `complete_syk_april3`, `april2_liberation_day_runbook`, etc.). Each executed one specific past trade and reached through the old `alpaca-trade-api` client. Archived during the migration to the modern `alpaca-py` SDK; the live trade path (`trader.py`, `trade_loop.py`, `dashboard/export.py`) was migrated in place. `tools/_activator_template.py` was kept + migrated as the template for future activations.

The local execution layer is now version-controlled launchd plists in `launchd/`
(trade loop + scanners); research runs as cloud routines (see `CLOUD_ROUTINE.md`).
