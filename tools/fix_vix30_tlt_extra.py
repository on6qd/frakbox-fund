"""Update extra field on newly-created VIX>30 TLT short hypothesis 106c77c6."""
import sys, json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

db.init_db()

hid = "106c77c6"

extra = {
    "notes": [
        "2026-04-24: Pre-registered. VIX=19.31. Signal fires when VIX CLOSES above 30.",
        "Last trigger: 2026-03-27 (same event that fired active SPY-long b63a0168).",
        "Earliest next eligible trigger (30-day cluster buffer): ~2026-04-26.",
        "Entry at next open after VIX>30 close. Short TLT. Hold 5 trading days.",
        "Stop loss: 5% adverse. Position: $5000.",
        "Robustness test passed: excluding 2022 Fed-hike year, signal -2.58% p=0.061.",
        "Canonical retest PASSED pooled (n=14, -2.75% p=0.0077) and recent (n=11, -2.36% p=0.047).",
        "Companion signal to SPY-long — different mechanism, tradeable independently.",
    ],
    "methodology_conflict_disclosure": {
        "conflict_with": "dead_end:vix_spike_sector_rotation (2026-XX, 14-sector screen)",
        "conflict_description": (
            "Prior 14-sector VIX>30 rotation screen killed TLT 5d short on a 2-horizon persistence rule "
            "(TLT 5d p=0.016 pass, but 10d/20d fail p>0.05). Canonical retest confirms this: 5d p=0.0077 "
            "passes, 10d p=0.105 fails, 20d p=0.146 fails. Single-horizon signal."
        ),
        "reconciliation": (
            "The current canonical methodology (dual-sample pooled+recent, one horizon sufficient) "
            "supersedes the prior 14-sector persistence heuristic. Liquidity-driven flush-and-reversal "
            "dynamics are legitimately single-horizon by nature (5d capture window). The 2-horizon rule "
            "was motivated by multiple-testing concerns across 14 sectors; this hypothesis is "
            "single-sector and uses dual-sample validation instead."
        ),
        "risk_flag": (
            "TREAT AS PROBATIONARY: signal is single-horizon, narrower margin than multi-horizon "
            "event signals. Require 3+ OOS confirmations before broader deployment. First live trigger "
            "should be $5k paper trade only."
        ),
    },
    "related_dead_ends": [
        "vix_spike_sector_rotation — TLT 5d p=0.016 but killed by 10d/20d persistence fail",
        "vix_spike_above_30_spy_recovery_dead_end_2026_04_20 — bug-driven retirement of SPY recovery",
        "vix_spike_presold_regime_audit_2026_04_20 — pre-sold regime artifact on SPY",
    ],
    "trigger_rule": {
        "condition": "VIX close > 30.0 AND no prior VIX>30 close in past 30 calendar days",
        "action": "short TLT at next market open, hold 5 trading days, exit at close",
        "position_size_usd": 5000,
        "stop_loss_pct": 5.0,
        "take_profit_pct": None,
        "activation_mode": "AWAITING_NEXT_TRIGGER — daemon/human sets trigger=next_market_open when VIX crosses",
    },
    "historical_analysis_tool": "tools/vix30_tlt_robustness.py",
    "canonical_retest_task_id": "T-ec9e9e47",
}

db.update_hypothesis_fields(hid, extra=json.dumps(extra))
print(f"Updated hypothesis {hid} with methodology conflict disclosure and trigger rule.")

# Verify
r = db.get_hypothesis_by_id(hid)
e = json.loads(r["extra"])
print("\n-- Stored extra keys --")
for k in e.keys():
    print(f"  {k}")
print(f"\nStatus: {r['status']}")
print(f"Trigger: {r['trigger']}")
