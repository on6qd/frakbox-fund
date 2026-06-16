"""
Scan-hit dead-end de-duplication helper.

Scanners should call `is_known_dead_end(signal_key, tickers=...)` before queuing
a scan_hit into research_queue. The check consults known_effects for any entry
whose status contains DEAD_END and whose data/finding mentions the same
tickers/pair. This prevents the orchestrator from wasting a session on a
signal that was already resolved.

Driven by the 2026-04-24 BA-RTX re-discovery (see
cointegration_scan_hit_batch_closure_2026_04_24).

Usage (in a scanner script):
    from tools.scan_hit_dead_end_check import is_known_dead_end
    hit, reason = is_known_dead_end(pair=("BA", "RTX"))
    if hit:
        print(f"SKIP BA-RTX: {reason}")
        continue
    add_research_task("scan_hit", "...", priority, reasoning)
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db


def _all_dead_end_entries():
    """Return list of (source_id, blob_text) where blob_text is a text blob
    containing the entry's dead-end context. Draws from:
      - known_effects where status contains DEAD_END/DISPROVEN/REJECTED
      - research_queue closed scan_hits with DEAD_END status
    """
    conn = db.get_db()
    out = []

    # known_effects
    for r in conn.execute("SELECT event_type, data FROM known_effects").fetchall():
        try:
            d = json.loads(r[1]) if r[1] else {}
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {"raw": str(d)}
        status = str(d.get("status", "")).upper()
        name_status = r[0].upper()
        blob = status + " " + name_status
        if "DEAD_END" in blob or "DISPROVEN" in blob or "REJECTED" in blob:
            out.append((r[0], r[0] + " " + json.dumps(d), d.get("status")))

    # closed scan_hits that reached a dead-end terminal status
    for r in conn.execute(
        "SELECT id, question, status, findings FROM research_queue "
        "WHERE category='scan_hit' AND status LIKE '%DEAD_END%'"
    ).fetchall():
        blob = " ".join([r[0] or "", r[1] or "", r[3] or ""])
        out.append((f"scan_hit:{r[0]}", blob, r[2]))

    return out


def _mentions_all(text, tokens):
    """Word-boundary AND match: all tokens must appear in text as whole words.
    Tickers are short and uppercase, so plain substring matching produces false
    positives (e.g. 'V' matches inside 'Voluntary', 'MA' inside 'margin').
    """
    if not text or not tokens:
        return False
    for t in tokens:
        # word boundary, exact case preserved for tickers (they should appear
        # in uppercase in the stored payload)
        patt = r"\b" + re.escape(t) + r"\b"
        if not re.search(patt, text):
            return False
    return True


def is_known_dead_end(signal_key=None, pair=None, tickers=None):
    """
    Check if a candidate signal or pair is already a recorded dead end.

    Args:
        signal_key: optional string — e.g. 'vix30_xlk_threshold' — matched against
            event_type keys in known_effects.
        pair: optional tuple/list of 2 tickers — e.g. ('BA', 'RTX').
        tickers: optional list of tickers to match (used if the hypothesis
            involves a single or N-asset signal).

    Returns:
        (is_dead_end: bool, reason: str) — reason is empty when not dead.
    """
    entries = _all_dead_end_entries()
    tokens = []
    if pair:
        tokens = [pair[0], pair[1]]
    elif tickers:
        tokens = list(tickers)

    # Direct key match
    if signal_key:
        lk = signal_key.lower()
        for name, blob, status in entries:
            if lk in name.lower() or name.lower() in lk:
                return True, f"event_type match: {name} (status={status})"

    # Token (ticker) co-occurrence match in event_type or data blobs
    if tokens:
        for name, blob, status in entries:
            if _mentions_all(blob, tokens):
                return True, (
                    f"tickers {'/'.join(tokens)} mentioned in dead-end record "
                    f"{name} (status={status})"
                )

    return False, ""


# ---------------------------------------------------------------------------
# Rule-based systematic lead-lag dead-end classifier
# ---------------------------------------------------------------------------
# Ticker co-occurrence (is_known_dead_end) only fires when a PRIOR record names
# the exact pair. But Granger lead-lag scan hits are a documented SYSTEMATIC
# dead end by STRUCTURE, not by specific pair. The 2026-06-16 lead-lag scan
# re-queued 33 hits, of which co-occurrence caught only 13 — the other 20 were
# the same regime-flip / contemporaneous-beta / non-synchronous-trading
# artifacts, just with pairs no prior record happened to mention. This
# classifier encodes the structural rules so the WHOLE FAMILY auto-suppresses.
#
# Backing knowledge:
#   dgs10_granger_lead_lag_systematic_dead_end (rates/macro -> sector: 2022 sign-flip)
#   commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20
#   leadlag_same_index_or_same_sector_auto_suppress_rule_2026_04_22
#   factor_exposure_contemporaneous_not_tradeable_rule_2026_06_09

# Macro / rates / FX drivers: contemporaneous beta with regime-dependent sign.
_RATE_FX_DRIVERS = {
    # rate / bond proxies
    "TLT", "IEF", "SHY", "IEI", "TLH", "GOVT", "BIL", "AGG", "BND", "LQD",
    "HYG", "JNK", "TIP", "MUB", "EMB",
    # dollar / FX ETFs
    "UUP", "UDN", "FXE", "FXB", "FXY", "FXA", "FXC", "FXF", "CYB", "DXY",
}

_COMMODITY = {
    # futures
    "CL=F", "BZ=F", "HG=F", "GC=F", "SI=F", "NG=F", "PA=F", "PL=F",
    "ZS=F", "ZW=F", "ZC=F", "ZL=F", "ZM=F", "KC=F", "CT=F", "SB=F", "CC=F",
    # commodity / miner ETFs
    "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG", "DBA", "DBC", "DBO", "CPER",
    "PPLT", "PALL", "SIL", "URA", "WEAT", "CORN", "SOYB", "UGA",
}

_SECTOR_ETF = {
    "XLF", "XLE", "XLI", "XLU", "XLP", "XLY", "XLV", "XLK", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "XME", "KRE", "KBE", "IBB", "XBI", "MOO", "ITB",
    "XHB", "XRT", "OIH", "XOP", "VNQ", "IYR",
}

_BROAD_INDEX = {
    "SPY", "IWM", "QQQ", "DIA", "RSP", "VTI", "VOO", "IVV", "MDY", "IJH",
    "IJR", "OEF", "ONEQ", "VEU", "ACWI",
}

# Single-country / regional equity ETFs — Granger lead-lag across these is a
# non-synchronous (different-timezone) trading artifact, not a tradeable edge.
_INTL_EQUITY = {
    "VGK", "EEM", "EFA", "VEA", "VWO", "EZU", "FXI", "MCHI", "INDA", "EWUK",
    "EWJ", "EWG", "EWU", "EWZ", "EWA", "EWC", "EWH", "EWW", "EWT", "EWY",
    "EWP", "EWI", "EWQ", "EWL", "EWD", "EWN", "EWS", "EWM", "EWK", "EZA",
    "EIDO", "THD", "TUR", "EPOL", "EPU", "ECH", "GREK", "EWO",
}


def _is_macro_fred(sym):
    s = (sym or "").upper()
    return s.startswith("FRED:") or s.startswith("FF:")


def is_leadlag_systematic_dead_end(driver, target):
    """Rule-based check: is a Granger lead-lag (driver -> target) a member of a
    documented SYSTEMATIC dead-end family? Returns (bool, reason).

    These families have been exhaustively shown to produce Granger significance
    in-sample that vanishes OOS and never crosses the 1% P&L magnitude floor,
    because the relationship is a contemporaneous beta (priced in real time)
    whose sign is regime-dependent, or a non-synchronous-trading artifact.
    """
    d = (driver or "").upper().strip()
    t = (target or "").upper().strip()
    if not d or not t:
        return False, ""

    # 1. Macro / rates / FX driver -> any asset: contemporaneous-beta regime flip
    if _is_macro_fred(d) or d in _RATE_FX_DRIVERS:
        return True, (
            f"macro/rates/FX driver {d} -> {t} is a SYSTEMATIC lead-lag dead end "
            "(contemporaneous beta with regime-dependent sign; vanishes OOS). "
            "See dgs10_granger_lead_lag_systematic_dead_end."
        )

    # 2. Commodity on either leg: exposure artifact amplified by vol serial corr
    if d in _COMMODITY or t in _COMMODITY:
        return True, (
            f"commodity leg in {d} -> {t} is a SYSTEMATIC lead-lag dead end "
            "(contemporaneous exposure + volatility serial correlation; P&L "
            "never crosses 1%). See "
            "commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20."
        )

    # 3. Sector-rotation (both sector ETFs) or broad-index co-movement
    if (d in _SECTOR_ETF and t in _SECTOR_ETF) or (
        d in _BROAD_INDEX and t in _BROAD_INDEX
    ):
        return True, (
            f"same-class index/sector pair {d} -> {t} auto-suppressed "
            "(shared-factor risk-on/risk-off co-movement artifact). See "
            "leadlag_same_index_or_same_sector_auto_suppress_rule_2026_04_22."
        )

    # 4. International single-country/regional equity pair: non-synchronous trading
    if d in _INTL_EQUITY and t in _INTL_EQUITY:
        return True, (
            f"international equity pair {d} -> {t} is a non-synchronous-trading "
            "(timezone overlap) artifact, not a tradeable lead-lag."
        )

    return False, ""


def _cli():
    """CLI for ad-hoc checks:
        python3 tools/scan_hit_dead_end_check.py --pair BA RTX
        python3 tools/scan_hit_dead_end_check.py --tickers XLU TLT
        python3 tools/scan_hit_dead_end_check.py --signal-key vix30_xlk_threshold
    """
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--pair", nargs=2, metavar=("A", "B"))
    p.add_argument("--tickers", nargs="+")
    p.add_argument("--signal-key", default=None)
    p.add_argument(
        "--leadlag",
        nargs=2,
        metavar=("DRIVER", "TARGET"),
        help="check the rule-based systematic lead-lag classifier",
    )
    args = p.parse_args()

    if args.leadlag:
        hit, reason = is_leadlag_systematic_dead_end(*args.leadlag)
        print(json.dumps({"is_dead_end": hit, "reason": reason}, indent=2))
        return

    db.init_db()
    hit, reason = is_known_dead_end(
        signal_key=args.signal_key, pair=args.pair, tickers=args.tickers
    )
    print(json.dumps({"is_dead_end": hit, "reason": reason}, indent=2))


if __name__ == "__main__":
    _cli()
