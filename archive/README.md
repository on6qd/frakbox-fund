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

**Dangling references still in active code (to resolve in #14, daemon retirement):**
- `health_check.py` — `pgrep`s for `researcher.sh` and restarts via `start.sh`; this daemon-monitoring model is obsolete once the cloud routine + launchd jobs replace the daemon.
- `should_run.py` — was called by `researcher.sh` to gate whether a session has work; may be repurposed by the cloud routine.

Kept (not deleted) for reference until the launchd/routine setup is finalized.
