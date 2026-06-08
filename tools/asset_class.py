"""Asset-class classifier and benchmark resolver.

Motivation
----------
The threshold/backtest canonical retest historically hardcoded SPY as the
abnormal-return benchmark. For non-equity targets (Treasuries, commodities, FX,
crypto) SPY is the WRONG benchmark: subtracting SPY's own post-event drift
injects an equity-market signal into the "abnormal" return of an unrelated
asset. This produced false canonical PASSes — e.g. VIX>35 -> TLT short, where
the apparent edge was entirely SPY's +3.18% post-capitulation bounce, not any
TLT move (see threshold_canonical_retest_nonequity_benchmark_invalid_rule_2026_06_08
and the two dead-end sessions on 2026-06-08).

Guard
-----
`resolve_event_benchmark(symbol)` returns the benchmark to subtract:
  - "SPY"  for equities (stocks, equity ETFs, equity indices) — unchanged behavior
  - None   for non-equities (treasury, commodity, fx, crypto) — use RAW returns,
           because there is no single correct cross-asset benchmark and the
           threshold signal is tested on the asset's own move.

When benchmark is None, measure_event_impact treats abnormal == raw.
"""

# Explicit non-equity sets (ETFs trade as equities mechanically, but they TRACK
# a non-equity asset, so SPY-adjustment is meaningless for them).
_TREASURY_BOND = {
    "TLT", "IEF", "SHY", "IEI", "TLH", "GOVT", "BIL", "SHV", "EDV", "ZROZ",
    "VGIT", "VGLT", "VGSH", "SCHO", "SCHR", "SCHQ", "SPTL", "SPTS",
    "AGG", "BND", "BNDX", "LQD", "HYG", "JNK", "TIP", "VTIP", "SCHP",
    "MUB", "EMB", "VCIT", "VCSH", "VCLT", "MBB", "BIV", "BSV", "BLV",
}
_COMMODITY = {
    "GLD", "IAU", "SLV", "SIVR", "USO", "UNG", "DBC", "GSG", "PDBC", "DBA",
    "DBB", "DBO", "BNO", "UGA", "CPER", "PPLT", "PALL", "CORN", "WEAT",
    "SOYB", "WOOD", "URA", "REMX", "COMT", "BCI", "GLDM", "SGOL", "USCI",
}
_FX_ETF = {"UUP", "UDN", "FXE", "FXY", "FXB", "FXF", "FXA", "FXC", "CYB"}
_CRYPTO = {
    "GBTC", "BITO", "IBIT", "ETHE", "FBTC", "ARKB", "BITB", "BTCO", "EZBC",
    "HODL", "BRRR", "BTCW", "ETHA", "ETHW",
}
# Index futures behave like the equity index; SPY-benchmark is fine for them.
_INDEX_FUTURES = {"ES=F", "NQ=F", "YM=F", "RTY=F"}

# Broad equity indices / equity ETFs that correctly use SPY.
_EQUITY_INDICES = {
    "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "RSP", "MDY", "VTV", "VUG",
    "^GSPC", "^NDX", "^DJI", "^RUT",
}


def classify_asset(symbol: str) -> str:
    """Return one of: treasury, commodity, fx, crypto, equity.

    Heuristics, in priority order:
      - explicit sets above
      - suffix rules: '=X' -> fx; '=F' -> commodity (unless index future);
        '-USD' -> crypto
      - default -> equity (stocks, equity ETFs, sector SPDRs, equity indices)
    """
    if not symbol:
        return "equity"
    s = symbol.strip().upper()

    if s in _TREASURY_BOND:
        return "treasury"
    if s in _COMMODITY:
        return "commodity"
    if s in _FX_ETF:
        return "fx"
    if s in _CRYPTO:
        return "crypto"
    if s in _INDEX_FUTURES or s in _EQUITY_INDICES:
        return "equity"

    # Suffix-based rules
    if s.endswith("=X"):
        return "fx"
    if s.endswith("=F"):
        return "commodity"
    if s.endswith("-USD") or s.endswith("-USDT"):
        return "crypto"
    # Dollar-index special cases
    if s in {"DX-Y.NYB", "DXY", "^DXY"}:
        return "fx"

    return "equity"


def is_equity(symbol: str) -> bool:
    return classify_asset(symbol) == "equity"


def resolve_event_benchmark(symbol: str, default: str = "SPY"):
    """Resolve the abnormal-return benchmark for an event study target.

    Returns `default` ("SPY") for equities, None for non-equities (raw returns).
    """
    return default if is_equity(symbol) else None


if __name__ == "__main__":
    import sys
    for sym in sys.argv[1:] or ["TLT", "GLD", "USO", "CL=F", "EURUSD=X", "BTC-USD",
                                 "AAPL", "SPY", "XLE", "GDX", "UUP", "ES=F"]:
        print(f"{sym:12s} -> {classify_asset(sym):10s} | benchmark="
              f"{resolve_event_benchmark(sym)}")
