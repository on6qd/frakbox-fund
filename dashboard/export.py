#!/usr/bin/env python3
"""Export research data to static JSON for the Frakbox dashboard."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import config

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _atomic_write(path, data):
    """Write JSON atomically via temp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def export_fund():
    """Build fund.json: NAV history, performance metrics, risk status."""
    data = {"nav_history": [], "current": {}, "performance": {}, "risk": {}}
    alpaca_ok = False

    # Current account state from Alpaca
    try:
        import trader
        acct = trader.get_account_summary()
        data["current"] = {
            "equity": acct["equity"],
            "cash": acct["cash"],
            "buying_power": acct["buying_power"],
        }
        # Snapshot today's NAV
        today = datetime.now().strftime("%Y-%m-%d")
        db.snapshot_nav(today, acct["equity"], acct["cash"], len(acct["positions"]))
        alpaca_ok = True
    except Exception as e:
        print(f"[export] Alpaca unavailable: {e}", file=sys.stderr)

    # NAV history from snapshots
    data["nav_history"] = db.get_nav_history()

    # Performance from completed hypotheses (exclude research-only completions)
    completed = db.get_hypotheses_by_status("completed")
    traded = [h for h in completed if h.get("trade") and (h.get("trade") or {}).get("entry_price", 0) >= 0.1]
    research_only = [h for h in completed if h not in traded]
    if traded:
        wins = [h for h in traded if h.get("result", {}).get("direction_correct")]
        losses = [h for h in traded if not h.get("result", {}).get("direction_correct")]
        win_returns = [h["result"]["raw_return_pct"] for h in wins if h.get("result", {}).get("raw_return_pct") is not None]
        loss_returns = [h["result"]["raw_return_pct"] for h in losses if h.get("result", {}).get("raw_return_pct") is not None]

        data["performance"] = {
            "total_trades": len(traded),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(len(wins) / len(traded) * 100, 1) if traded else 0,
            "avg_win_pct": round(sum(win_returns) / len(win_returns), 2) if win_returns else 0,
            "avg_loss_pct": round(sum(loss_returns) / len(loss_returns), 2) if loss_returns else 0,
            "direction_accuracy": f"{len(wins)}/{len(traded)}",
            "research_only_completions": len(research_only),
        }

        # Total return and drawdown from NAV history
        nav = data["nav_history"]
        if len(nav) >= 2:
            first_equity = nav[0]["equity"]
            last_equity = nav[-1]["equity"]
            data["performance"]["total_return_pct"] = round(
                (last_equity - first_equity) / first_equity * 100, 2
            )
            # Max drawdown from NAV series
            peak = nav[0]["equity"]
            max_dd = 0
            for point in nav:
                if point["equity"] > peak:
                    peak = point["equity"]
                dd = (peak - point["equity"]) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            data["performance"]["max_drawdown_pct"] = round(max_dd, 2)

            # Sharpe estimate (annualized) — need 20+ daily points
            if len(nav) >= 20:
                daily_returns = []
                for i in range(1, len(nav)):
                    r = (nav[i]["equity"] - nav[i - 1]["equity"]) / nav[i - 1]["equity"]
                    daily_returns.append(r)
                if daily_returns:
                    import statistics
                    mean_r = statistics.mean(daily_returns)
                    std_r = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 1
                    if std_r > 0:
                        data["performance"]["sharpe_estimate"] = round(
                            mean_r / std_r * (252 ** 0.5), 2
                        )

    # Risk status
    try:
        import trader
        dd = trader.check_portfolio_drawdown()
        data["risk"] = {
            "drawdown_pct": dd.get("drawdown_pct", 0),
            "drawdown_limit_pct": config.MAX_PORTFOLIO_DRAWDOWN_PCT,
            "safe_to_trade": dd.get("safe_to_trade", False),
            "max_position_pct": config.MAX_POSITION_PCT * 100,
            "stop_loss_pct": config.DEFAULT_STOP_LOSS_PCT,
            "max_concurrent": config.MAX_CONCURRENT_EXPERIMENTS,
            "active_positions": len(db.get_hypotheses_by_status("active")),
        }
    except Exception:
        data["risk"] = {
            "drawdown_limit_pct": config.MAX_PORTFOLIO_DRAWDOWN_PCT,
            "max_position_pct": config.MAX_POSITION_PCT * 100,
            "stop_loss_pct": config.DEFAULT_STOP_LOSS_PCT,
            "max_concurrent": config.MAX_CONCURRENT_EXPERIMENTS,
        }

    _atomic_write(os.path.join(DATA_DIR, "fund.json"), data)
    return alpaca_ok


def export_positions():
    """Build positions.json: delayed active positions + recent closed trades."""
    data = {"active": [], "recent_closed": []}

    # Active positions — deliberately limited info
    for h in db.get_hypotheses_by_status("active"):
        data["active"].append({
            "symbol": h.get("expected_symbol", ""),
            "direction": h.get("expected_direction", ""),
            "event_type": h.get("event_type", ""),
            "thesis": (h.get("event_description") or "")[:120],
            "opened_date": (h.get("trade", {}) or {}).get("entry_date", h.get("created", "")[:10]),
        })

    # Recent closed trades — full results (last 10, exclude research-only)
    completed = db.get_hypotheses_by_status("completed")
    traded = [h for h in completed if h.get("trade") and (h.get("trade") or {}).get("entry_price", 0) >= 0.1]
    traded.sort(key=lambda h: h.get("result", {}).get("exit_time", ""), reverse=True)
    for h in traded[:10]:
        result = h.get("result", {}) or {}
        data["recent_closed"].append({
            "symbol": h.get("expected_symbol", ""),
            "direction": h.get("expected_direction", ""),
            "event_type": h.get("event_type", ""),
            "result_pct": result.get("raw_return_pct"),
            "abnormal_pct": result.get("abnormal_return_pct"),
            "exit_reason": result.get("exit_reason", ""),
            "closed_date": (result.get("exit_time") or "")[:10],
            "thesis": (h.get("event_description") or "")[:120],
            "direction_correct": result.get("direction_correct"),
        })

    _atomic_write(os.path.join(DATA_DIR, "positions.json"), data)


def _classify_journal_entry(j):
    """Classify a journal entry by what the session accomplished.

    Tags reflect the research lifecycle:
    - discovery:      found a new tradeable signal
    - dead_end:       conclusively rejected a hypothesis
    - validation:     OOS or live validation of existing signal
    - exploration:    early investigation, data gathering, literature
    - operational:    trade execution, monitoring, scanner checks
    - infrastructure: tool building, bug fixes, methodology improvements
    """
    findings = (j.get("findings") or "").upper()
    public = (j.get("public_summary") or "").upper()
    text = findings + " " + public
    session = (j.get("session_type") or "").lower()

    dead_markers = ["DEAD END", "DEAD_END", "NO SIGNAL", "NULL RESULT",
                    "FAILS MULTIPLE TESTING", "NO EDGE"]

    discovery_markers = ["HYPOTHESIS CREATED", "FORMALIZED",
                         "SIGNAL VALIDATED", "PASSES MULTIPLE TESTING"]

    validation_markers = ["OOS PASS", "LIVE VALIDATION", "OOS CONFIRMED",
                          "OOS VALIDATION"]
    validation_sessions = ["backtest_validation", "oos_validation",
                           "signal_validation", "oos_measurement",
                           "oos_tracking_setup"]

    exploration_sessions = ["feasibility_study", "literature_review",
                            "signal_discovery", "exploratory_research",
                            "data_collection", "signal_research",
                            "signal_testing", "hypothesis_testing",
                            "regime_analysis", "backtest", "backtest_expansion",
                            "mechanism_testing", "signal_investigation"]

    infra_sessions = ["infrastructure", "tool_build", "maintenance",
                      "friction_fix"]

    ops_sessions = ["trade_management", "trade_monitoring", "monitoring",
                    "status_check", "operational_scan", "trade_setup",
                    "trade_status"]

    has_dead = any(m in text for m in dead_markers)
    has_discovery = any(m in text for m in discovery_markers)
    has_validation = (any(m in text for m in validation_markers)
                      or any(s in session for s in validation_sessions))
    is_exploration = any(s in session for s in exploration_sessions)
    is_infra = any(s in session for s in infra_sessions)
    is_ops = any(s in session for s in ops_sessions)

    # Priority: dead_end > discovery > validation > ops > infra > exploration
    if has_dead and not has_discovery:
        return "dead_end"
    if has_discovery and not has_dead:
        return "discovery"
    if has_validation and not has_dead:
        return "validation"
    if is_ops:
        return "operational"
    if is_infra:
        return "infrastructure"
    if is_exploration:
        return "exploration"
    # Default: most sessions are exploration
    return "exploration"


def export_research():
    """Build research.json: hypothesis stats, knowledge, journal, activity, pipeline."""
    import research

    data = {}

    # Summary stats
    data["summary"] = research.get_research_summary()

    # Knowledge base — filter to meaningful signals only
    kb = db.load_knowledge()
    signals = []
    for name, effect in kb.get("known_effects", {}).items():
        status = effect.get("status", "unknown")
        mag = effect.get("avg_magnitude_pct")
        if status in ("strong", "moderate") or mag is not None:
            signals.append({
                "name": name,
                "status": status,
                "magnitude_pct": mag,
            })
    dead_ends = []
    for de in kb.get("dead_ends", []):
        dead_ends.append({
            "name": de.get("event_type", ""),
            "reason": de.get("reason", ""),
        })
    data["knowledge"] = {
        "signals": signals,
        "dead_ends": dead_ends,
        "signal_count": len(signals),
        "dead_end_count": len(dead_ends),
        "literature_count": len(kb.get("literature", {})),
    }

    # Live signal tests — pattern lifecycle data
    patterns = db.load_patterns()
    live_signals = []
    for event_type, pat in patterns.items():
        exps = pat.get("experiments", [])
        live_signals.append({
            "event_type": event_type,
            "state": pat.get("state", "EXPLORING"),
            "total_experiments": pat.get("total_tests", 0),
            "effective_independent_n": pat.get("effective_independent_n", pat.get("total_tests", 0)),
            "effective_correct_n": pat.get("effective_correct_n", pat.get("direction_correct_count", 0)),
            "last_updated": pat.get("last_updated", ""),
            "experiments": [
                {
                    "symbol": e.get("symbol", ""),
                    "correct": e.get("direction_correct", False),
                    "return_pct": e.get("actual_pct", 0),
                    "date": (e.get("date") or "")[:10],
                }
                for e in exps[-10:]
            ],
        })
    data["live_signals"] = live_signals

    # Focus status
    active_types = set()
    for h in db.get_hypotheses_by_status("active") + db.get_hypotheses_by_status("pending"):
        active_types.add(h.get("event_type", ""))
    data["focus"] = {
        "active_signal_types": len(active_types),
        "max_signal_types": config.MAX_ACTIVE_SIGNAL_TYPES,
        "over_limit": len(active_types) > config.MAX_ACTIVE_SIGNAL_TYPES,
    }

    # Activity — token usage by day (last 30 days)
    today = datetime.now()
    sessions_by_day = []
    for i in range(30):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        usage = db.get_daily_token_usage(d)
        if usage["sessions"] > 0:
            sessions_by_day.append({
                "date": d,
                "sessions": usage["sessions"],
                "tokens": usage["total_tokens"],
            })
    data["activity"] = {
        "sessions_today": db.get_daily_token_usage()["sessions"],
        "tokens_today": db.get_daily_token_usage()["total_tokens"],
        "sessions_by_day": sessions_by_day,
    }

    # Journal — all entries, using public_summary when available
    journal = db.get_recent_journal(9999)
    data["journal"] = [
        {
            "date": j.get("date", ""),
            "investigated": j.get("investigated", ""),
            "findings": j.get("public_summary") or j.get("findings", "")[:200],
            "tag": _classify_journal_entry(j),
        }
        for j in journal
    ]

    # Pipeline — what's coming next
    data["pipeline"] = _build_pipeline()

    _atomic_write(os.path.join(DATA_DIR, "research.json"), data)


def export_hypotheses():
    """Build hypotheses.json: all hypotheses with investigation reports."""
    import research

    all_hyps = db.load_hypotheses()
    # Sort: active first, then pending, completed, invalidated — newest first within each
    status_order = {"active": 0, "pending": 1, "completed": 2, "invalidated": 3}
    all_hyps.sort(key=lambda h: (status_order.get(h["status"], 9), h.get("created", "")),
                  reverse=False)
    # Within each status group, sort newest first
    all_hyps.sort(key=lambda h: (status_order.get(h["status"], 9),))

    hypotheses = []
    for h in all_hyps:
        # Generate report
        try:
            report = research.generate_investigation_report(h["id"])
        except Exception:
            report = None

        result = h.get("result") or {}
        trade = h.get("trade") or {}

        entry = {
            "id": h["id"],
            "status": h["status"],
            "created": (h.get("created") or "")[:10],
            "event_type": (h.get("event_type") or "").replace("_", " "),
            "symbol": h.get("expected_symbol", ""),
            "direction": h.get("expected_direction", ""),
            "magnitude_pct": h.get("expected_magnitude_pct"),
            "timeframe_days": h.get("expected_timeframe_days"),
            "confidence": h.get("confidence"),
            "thesis": (h.get("event_description") or "")[:150],
            "report": report,
        }

        # Add outcome fields for completed hypotheses
        if h["status"] == "completed" and result:
            entry["result_pct"] = result.get("raw_return_pct")
            entry["abnormal_pct"] = result.get("abnormal_return_pct")
            entry["direction_correct"] = result.get("direction_correct")
            entry["magnitude_ratio"] = result.get("magnitude_ratio")
            entry["closed_date"] = (result.get("exit_time") or "")[:10]

        # Add trade info for active hypotheses
        if h["status"] == "active" and trade:
            entry["entry_date"] = (trade.get("entry_time") or "")[:10]
            entry["deadline"] = (trade.get("deadline") or "")[:10]

        # Add invalidation reason
        if h["status"] == "invalidated" and result:
            entry["invalidation_reason"] = (result.get("reason") or "")[:200]

        hypotheses.append(entry)

    data = {
        "hypotheses": hypotheses,
        "counts": {
            "total": len(hypotheses),
            "active": sum(1 for h in hypotheses if h["status"] == "active"),
            "pending": sum(1 for h in hypotheses if h["status"] == "pending"),
            "completed": sum(1 for h in hypotheses if h["status"] == "completed"),
            "abandoned": sum(1 for h in hypotheses if h["status"] == "abandoned"),
            "invalidated": sum(1 for h in hypotheses if h["status"] == "invalidated"),
            "superseded": sum(1 for h in hypotheses if h["status"] == "superseded"),
        },
    }
    _atomic_write(os.path.join(DATA_DIR, "hypotheses.json"), data)


def _build_pipeline():
    """Build the pipeline section: watchlist events, pending triggers, research queue."""
    conn = db.get_db()
    pipeline = {}

    # Upcoming watchlist events (next 30 days, sorted by date)
    cutoff = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT event, expected_date, symbol, status FROM event_watchlist "
        "WHERE status = 'watching' AND expected_date <= ? "
        "ORDER BY expected_date LIMIT 15",
        (cutoff,)
    ).fetchall()
    pipeline["watchlist"] = [
        {"event": r[0][:120], "date": r[1], "symbol": r[2]}
        for r in rows
    ]

    # Pending triggers — trades queued to auto-execute
    pending_with_triggers = [
        h for h in db.get_hypotheses_by_status("pending")
        if h.get("trigger")
    ]
    pipeline["pending_triggers"] = [
        {
            "symbol": h.get("expected_symbol", ""),
            "direction": h.get("expected_direction", ""),
            "trigger": h.get("trigger", ""),
            "event_type": (h.get("event_type") or "").replace("_", " "),
        }
        for h in pending_with_triggers[:10]
    ]

    # Top research questions
    rows = conn.execute(
        "SELECT question, priority, category FROM research_queue "
        "WHERE status = 'pending' ORDER BY priority DESC LIMIT 5"
    ).fetchall()
    pipeline["research_queue"] = [
        {"question": r[0][:150], "priority": r[1], "category": r[2]}
        for r in rows
    ]

    # Session handoff — agent's own "what to do next"
    row = conn.execute(
        "SELECT data FROM session_handoff ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        handoff_raw = row[0]
        # Extract key upcoming dates section if present
        try:
            handoff = json.loads(handoff_raw) if handoff_raw.startswith("{") else handoff_raw
        except (json.JSONDecodeError, AttributeError):
            handoff = handoff_raw
        if isinstance(handoff, str) and len(handoff) > 500:
            # Trim to the most useful part
            handoff = handoff[:500]
        pipeline["handoff"] = handoff
    else:
        pipeline["handoff"] = None

    return pipeline


def export_meta(alpaca_ok):
    """Build meta.json: export timestamp, health."""
    data = {
        "exported_at": datetime.now().isoformat(),
        "alpaca_connected": alpaca_ok,
        "export_version": 2,
    }
    _atomic_write(os.path.join(DATA_DIR, "meta.json"), data)


PUBLIC_REPO = Path.home() / "Bots" / "frakbox.io"
DASHBOARD_DIR = Path(__file__).parent


def sync_to_public_repo():
    """Copy static files + data to the public GitHub Pages repo and push."""
    if not PUBLIC_REPO.exists():
        print("[export] Public repo not found at {PUBLIC_REPO}, skipping sync", file=sys.stderr)
        return
    try:
        # Static files (only if changed)
        for f in ("index.html", "style.css", "app.js"):
            src = DASHBOARD_DIR / f
            dst = PUBLIC_REPO / f
            if src.exists():
                shutil.copy2(src, dst)

        # Data files
        dst_data = PUBLIC_REPO / "data"
        dst_data.mkdir(exist_ok=True)
        for f in Path(DATA_DIR).glob("*.json"):
            shutil.copy2(f, dst_data / f.name)

        # CNAME for custom domain
        cname = PUBLIC_REPO / "CNAME"
        if not cname.exists():
            cname.write_text("dashboard.frakbox.io\n")

        # Git add, commit, push (skip if nothing changed)
        subprocess.run(["git", "add", "-A"], cwd=PUBLIC_REPO, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PUBLIC_REPO, capture_output=True,
        )
        if result.returncode != 0:  # there are staged changes
            subprocess.run(
                ["git", "commit", "-m", f"data update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                cwd=PUBLIC_REPO, capture_output=True,
            )
            subprocess.run(
                ["git", "push"],
                cwd=PUBLIC_REPO, capture_output=True, timeout=30,
            )
            print("[export] Pushed to public repo")
        else:
            print("[export] No changes to push")
    except Exception as e:
        print(f"[export] Sync failed: {e}", file=sys.stderr)


def backfill_nav():
    """One-time: backfill NAV history from Alpaca portfolio history API."""
    try:
        import trader
        history = trader.get_portfolio_history(period="all", timeframe="1D")
        if history and history.equity:
            for ts, equity in zip(history.timestamp, history.equity):
                if equity is None:
                    continue
                date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                # Alpaca doesn't give cash breakdown in history, use 0
                db.snapshot_nav(date, float(equity), 0, 0)
            print(f"[export] Backfilled {len(history.equity)} NAV snapshots")
    except Exception as e:
        print(f"[export] Backfill failed: {e}", file=sys.stderr)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    db.init_db()
    config.load_env()

    alpaca_ok = False
    try:
        alpaca_ok = export_fund()
    except Exception as e:
        print(f"[export] fund.json failed: {e}", file=sys.stderr)

    try:
        export_positions()
    except Exception as e:
        print(f"[export] positions.json failed: {e}", file=sys.stderr)

    try:
        export_research()
    except Exception as e:
        print(f"[export] research.json failed: {e}", file=sys.stderr)

    try:
        export_hypotheses()
    except Exception as e:
        print(f"[export] hypotheses.json failed: {e}", file=sys.stderr)

    export_meta(alpaca_ok)
    sync_to_public_repo()
    print(f"[export] Done at {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        db.init_db()
        config.load_env()
        backfill_nav()
    else:
        main()
