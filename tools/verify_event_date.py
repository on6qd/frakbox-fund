"""
verify_event_date.py - Find the actual crash/event date for a stock near an approximate date.

Usage:
    python tools/verify_event_date.py TICKER APPROX_DATE [--threshold 30] [--window 10]

Given a ticker and approximate date, finds the actual large-move day within
+/- window trading days. Returns verified date, pre-crash close, and the crash magnitude.

This tool prevents the recurring error of using wrong event dates in backtests.
Sessions 3-5 wasted ~15 turns correcting dates that could have been auto-verified.

Examples:
    python tools/verify_event_date.py APLT 2024-02-14 --threshold 50
    python tools/verify_event_date.py ARDX 2021-07-12 --threshold 40
    python tools/verify_event_date.py NERV 2022-09-27 --threshold 30
"""

import sys
import os
import argparse
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_data


def verify_event_date(ticker: str, approx_date: str,
                      threshold_pct: float = 30.0,
                      window_days: int = 10,
                      direction: str = "both") -> dict:
    """
    Find the actual event date near approx_date where the stock moved by >= threshold_pct.

    Args:
        ticker: Stock ticker symbol
        approx_date: Approximate date string YYYY-MM-DD
        threshold_pct: Minimum % move to qualify as an event (default: 30%)
        window_days: Search +/- this many calendar days from approx_date (default: 10)
        direction: "down" (crashes only), "up" (rallies only), "both" (default)

    Returns:
        dict with:
            verified_date: The actual event date (or None)
            pre_event_close: Close price the day before the event
            post_event_close: Close price on the event day
            move_pct: Magnitude of the move (negative = crash)
            days_from_approx: How far off the original date was
            nearby_moves: All moves within window sorted by magnitude
    """
    event_dt = datetime.strptime(approx_date, "%Y-%m-%d")
    start = (event_dt - timedelta(days=window_days + 20)).strftime("%Y-%m-%d")
    end = (event_dt + timedelta(days=window_days + 20)).strftime("%Y-%m-%d")

    stock_df = market_data._fetch_stock_data(ticker, start, end)
    if stock_df.empty:
        return {
            "error": f"No data for {ticker} around {approx_date}",
            "ticker": ticker,
            "approx_date": approx_date
        }

    # Build sorted price series
    prices = []
    for d, row in stock_df.iterrows():
        date_str = d.strftime("%Y-%m-%d")
        prices.append({
            "date": date_str,
            "open": round(float(row["Open"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"])
        })

    prices.sort(key=lambda x: x["date"])

    # Compute daily moves (close-to-close)
    moves = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        curr = prices[i]
        if prev["close"] > 0:
            move_pct = ((curr["close"] - prev["close"]) / prev["close"]) * 100
            moves.append({
                "date": curr["date"],
                "pre_date": prev["date"],
                "pre_close": prev["close"],
                "post_close": curr["close"],
                "open": curr["open"],
                "volume": curr["volume"],
                "move_pct": round(move_pct, 2),
                "abs_move": abs(move_pct),
                "days_from_approx": abs((datetime.strptime(curr["date"], "%Y-%m-%d") - event_dt).days)
            })

    # Filter to window
    window_moves = [m for m in moves if m["days_from_approx"] <= window_days]

    # Filter by direction
    if direction == "down":
        qualifying = [m for m in window_moves if m["move_pct"] <= -threshold_pct]
    elif direction == "up":
        qualifying = [m for m in window_moves if m["move_pct"] >= threshold_pct]
    else:
        qualifying = [m for m in window_moves if m["abs_move"] >= threshold_pct]

    # Sort by magnitude (largest first)
    qualifying.sort(key=lambda x: x["abs_move"], reverse=True)
    window_moves.sort(key=lambda x: x["abs_move"], reverse=True)

    result = {
        "ticker": ticker,
        "approx_date": approx_date,
        "threshold_pct": threshold_pct,
        "window_days": window_days,
        "verified_date": None,
        "pre_event_close": None,
        "post_event_close": None,
        "open_on_event_day": None,
        "move_pct": None,
        "days_from_approx": None,
        "nearby_moves": [
            {
                "date": m["date"],
                "move_pct": m["move_pct"],
                "pre_close": m["pre_close"],
                "volume": m["volume"],
                "days_from_approx": m["days_from_approx"]
            }
            for m in window_moves[:5]
        ]
    }

    if qualifying:
        best = qualifying[0]
        result["verified_date"] = best["date"]
        result["pre_event_close"] = best["pre_close"]
        result["post_event_close"] = best["post_close"]
        result["open_on_event_day"] = best["open"]
        result["move_pct"] = best["move_pct"]
        result["days_from_approx"] = best["days_from_approx"]
        if best["days_from_approx"] > 0:
            result["date_was_wrong"] = True
            result["correction_note"] = (
                f"Approx date {approx_date} was {best['days_from_approx']} days off. "
                f"Actual event: {best['date']} ({best['move_pct']:+.1f}%)"
            )
        else:
            result["date_was_wrong"] = False
    else:
        result["no_qualifying_move"] = True
        result["note"] = (
            f"No move >= {threshold_pct}% found within {window_days} days of {approx_date}. "
            f"Largest move: {window_moves[0]['move_pct']:+.1f}% on {window_moves[0]['date']}"
            if window_moves else f"No data in window."
        )

    return result


def batch_verify(events: list, threshold_pct: float = 30.0, window_days: int = 10,
                 direction: str = "down") -> list:
    """
    Verify a batch of events.

    Args:
        events: List of dicts with 'ticker' and 'date' keys
        threshold_pct: Minimum move magnitude
        window_days: Search window
        direction: "down", "up", or "both"

    Returns:
        List of verification results
    """
    results = []
    for ev in events:
        ticker = ev.get("ticker") or ev.get("symbol")
        date = ev.get("date")
        print(f"  Verifying {ticker} around {date}...", end=" ", flush=True)
        r = verify_event_date(ticker, date, threshold_pct, window_days, direction)
        if r.get("error"):
            print(f"ERROR: {r['error']}")
        elif r.get("no_qualifying_move"):
            print(f"No qualifying move found. {r.get('note', '')}")
        else:
            status = "CORRECTED" if r.get("date_was_wrong") else "OK"
            print(f"{status}: {r['verified_date']} ({r['move_pct']:+.1f}%)")
        results.append(r)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Verify event date by finding actual crash/rally near approximate date"
    )
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("approx_date", help="Approximate date YYYY-MM-DD")
    parser.add_argument("--threshold", type=float, default=30.0,
                        help="Minimum % move magnitude (default: 30)")
    parser.add_argument("--window", type=int, default=10,
                        help="Search window in calendar days (default: 10)")
    parser.add_argument("--direction", choices=["down", "up", "both"], default="both",
                        help="Move direction to look for (default: both)")
    args = parser.parse_args()

    result = verify_event_date(args.ticker, args.approx_date,
                               args.threshold, args.window, args.direction)

    print(f"\n{'='*60}")
    print(f"Ticker: {result['ticker']}")
    print(f"Approx date: {result['approx_date']}")

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return

    if result.get("no_qualifying_move"):
        print(f"No qualifying move >= {args.threshold}% found in +/- {args.window} days")
        print(f"Note: {result.get('note', '')}")
    else:
        print(f"\nVERIFIED DATE: {result['verified_date']}")
        print(f"Pre-event close: ${result['pre_event_close']}")
        print(f"Post-event close: ${result['post_event_close']}")
        print(f"Open on event day: ${result['open_on_event_day']}")
        print(f"Move: {result['move_pct']:+.1f}%")
        print(f"Days from approx: {result['days_from_approx']}")
        if result.get("date_was_wrong"):
            print(f"\nWARNING: {result['correction_note']}")
        else:
            print(f"\nDate confirmed correct.")

    print(f"\nNearby moves (top 5 by magnitude):")
    for m in result.get("nearby_moves", []):
        print(f"  {m['date']}: {m['move_pct']:+.1f}% (pre: ${m['pre_close']}, vol: {m['volume']:,})")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
