"""
Health monitor for the research daemon.
Runs independently (via launchd) to detect daemon failures and enforce risk controls.

- Checks if daemon is alive
- Checks if sessions are running on schedule
- Runs stop-loss checks as safety net
- Sends alert emails on problems
- Auto-restarts daemon if it dies with active positions
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DAEMON_LOG = BASE_DIR / "logs" / "daemon.log"
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"
MAX_SILENCE_MINUTES = 120  # alert if no session in 2 hours

# Add project to path for imports
sys.path.insert(0, str(BASE_DIR))
import db as _db


def _load_state():
    _db.init_db()
    return _db.get_state('health_state') or {}


def _save_state(state):
    state["last_check"] = datetime.now().isoformat()
    _db.init_db()
    _db.set_state('health_state', state)


def _daemon_is_alive():
    """Check if researcher.sh is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "researcher.sh"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _last_session_time():
    """Parse daemon.log for the most recent session start or heartbeat time."""
    if not DAEMON_LOG.exists():
        return None
    try:
        text = DAEMON_LOG.read_text()
        # Match session starts and heartbeats (both prove daemon is active)
        matches = re.findall(
            r"=== (?:Session started|Heartbeat) (.+?) ===", text
        )
        if not matches:
            return None
        last = matches[-1].strip()
        # Strip any trailing bracketed metadata like [mode=scan agent=scanner session#12]
        last = re.sub(r"\s*\[.*\]\s*$", "", last)
        # Parse the date — strip timezone name (CET, EST, etc.)
        # Format: "Sat Mar 21 18:23:25 CET 2026"
        parts = last.split()
        if len(parts) >= 5:
            # Remove timezone name (4th element) if it's not a year
            try:
                int(parts[-1])  # last part should be year
                # Remove timezone abbreviation
                clean = " ".join(parts[:4] + parts[-1:])
                return datetime.strptime(clean, "%a %b %d %H:%M:%S %Y")
            except (ValueError, IndexError):
                pass
        return None
    except Exception:
        return None


def _has_active_positions():
    """Check if there are active hypotheses with open trades."""
    try:
        import db as _db
        return _db.count_hypotheses_by_status("active") > 0
    except Exception:
        return False


def _restart_daemon():
    """Restart the research daemon via start.sh (guards against duplicates)."""
    try:
        # Re-check right before spawning: _daemon_is_alive may have been
        # called seconds ago, and a concurrent health_check tick could have
        # already restarted the daemon. Without this, we've seen three
        # researcher.sh copies end up alive from stacked restarts.
        alive = subprocess.run(
            ["pgrep", "-f", "researcher.sh"],
            capture_output=True, text=True, timeout=5,
        )
        if alive.returncode == 0:
            with open(DAEMON_LOG, "a") as f:
                pids = alive.stdout.strip().replace("\n", ",")
                f.write(
                    f"\n=== health_check restart aborted at {datetime.now()}: "
                    f"daemon already alive (pids {pids}) ===\n"
                )
            return False

        with open(DAEMON_LOG, "a") as f:
            f.write(f"\n=== Daemon restarted by health_check.py at {datetime.now()} ===\n")

        # Use start.sh so it applies its own pgrep guard and nohup redirection
        # (single source of truth for daemon startup).
        subprocess.Popen(
            [str(BASE_DIR / "start.sh")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(BASE_DIR),
            start_new_session=True,
        )
        return True
    except Exception as e:
        print(f"Failed to restart daemon: {e}", file=sys.stderr)
        return False


def _send_alert(subject, body_text):
    """Send an alert email."""
    try:
        from email_report import send_email
        html = f"""
        <html><body style="font-family: -apple-system, Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #c62828;">Research System Alert</h2>
        <p style="color: #888;">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        <div style="background: #ffebee; border-left: 4px solid #c62828; padding: 12px 16px; margin: 12px 0;">
            {body_text}
        </div>
        <hr>
        <p style="color: #aaa; font-size: 11px;">Sent by health_check.py</p>
        </body></html>
        """
        send_email(subject, html)
        return True
    except Exception as e:
        print(f"Failed to send alert: {e}", file=sys.stderr)
        return False


def _send_recovery(body_text):
    """Send a recovery notification."""
    try:
        from email_report import send_email
        html = f"""
        <html><body style="font-family: -apple-system, Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2e7d32;">Research System Recovered</h2>
        <p style="color: #888;">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        <div style="background: #e8f5e9; border-left: 4px solid #2e7d32; padding: 12px 16px; margin: 12px 0;">
            {body_text}
        </div>
        <hr>
        <p style="color: #aaa; font-size: 11px;">Sent by health_check.py</p>
        </body></html>
        """
        send_email("Research system recovered", html)
    except Exception:
        pass


def run_health_check():
    """Main health check. Returns list of issues found."""
    state = _load_state()
    issues = []
    was_alerting = state.get("alerting", False)

    alive = _daemon_is_alive()
    last_session = _last_session_time()
    has_positions = _has_active_positions()

    now = datetime.now()

    # Check 1: Is daemon process alive?
    if not alive:
        issues.append(f"Daemon process (researcher.sh) is not running.")
        if has_positions:
            issues.append(f"Active positions exist — restarting daemon.")
            if _restart_daemon():
                issues.append("Daemon restarted successfully.")
            else:
                issues.append("FAILED to restart daemon.")

    # Check 2: Has a session run recently?
    if last_session:
        silence_minutes = (now - last_session).total_seconds() / 60
        if silence_minutes > MAX_SILENCE_MINUTES:
            hours = silence_minutes / 60
            issues.append(
                f"No session has run in {hours:.1f} hours "
                f"(last: {last_session.strftime('%H:%M')}, threshold: {MAX_SILENCE_MINUTES} min)."
            )
    elif alive:
        issues.append("Daemon is running but no sessions found in daemon.log.")

    # Check 3: Run stop-loss checks (safety net independent of daemon)
    if has_positions:
        try:
            from trader import check_stop_losses
            actions = check_stop_losses()
            for a in actions:
                if a["action"] in ("closed", "close_failed", "drawdown_alert"):
                    issues.append(
                        f"Stop-loss action: [{a['action']}] "
                        f"{a.get('symbol', '')} {a.get('reason', a.get('message', ''))}"
                    )
        except Exception as e:
            issues.append(f"Stop-loss check failed: {e}")

    # Send alerts or recovery
    if issues:
        if not was_alerting:
            # First alert — send email
            body = "<br>".join(f"<b>{i}</b>" if "FAIL" in i or "not running" in i else i for i in issues)
            _send_alert("Research system issue detected", body)
            state["alerting"] = True
            state["alert_start"] = now.isoformat()
            state["last_alert"] = now.isoformat()
        else:
            # Already alerting — re-alert every 30 minutes
            last_alert = state.get("last_alert", "")
            try:
                last_dt = datetime.fromisoformat(last_alert)
                if (now - last_dt).total_seconds() > 1800:
                    body = "<br>".join(issues)
                    _send_alert("Research system still has issues", body)
                    state["last_alert"] = now.isoformat()
            except (ValueError, TypeError):
                pass
    else:
        if was_alerting:
            _send_recovery("All systems operational. Daemon is running and sessions are on schedule.")
            state["alerting"] = False
            state.pop("alert_start", None)
            state.pop("last_alert", None)

    state["daemon_alive"] = alive
    state["last_session"] = last_session.isoformat() if last_session else None
    state["has_positions"] = has_positions
    state["issues"] = issues
    _save_state(state)

    return issues


if __name__ == "__main__":
    from config import load_env
    load_env()

    issues = run_health_check()
    if issues:
        for i in issues:
            print(f"  {i}")
    else:
        print("Healthy. Daemon running, sessions on schedule.")
