"""
Trading execution for research experiments.
Places and closes paper trades to test hypotheses.

Risk controls:
- Per-position stop-loss (default 10%)
- Per-position take-profit (optional)
- Portfolio-level max drawdown (default 15% from peak)
- Trade deadline enforcement (auto-close past deadline)
- check_stop_losses() runs independently of the LLM agent
"""

import json
import os
import sys
from datetime import datetime, timedelta

import db as _db
from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    MAX_POSITION_PCT, DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT,
    MAX_PORTFOLIO_DRAWDOWN_PCT, require_alpaca,
)

# Broker = Alpaca via the modern alpaca-py SDK. Imports are deferred into the
# client builders so this module loads even where alpaca-py isn't installed
# (e.g. cloud research routines, which never touch the broker).
_PAPER = "paper" in (ALPACA_BASE_URL or "")
_clients = {}


def _trading():
    """Lazily build & cache the alpaca-py TradingClient."""
    require_alpaca()
    if "trading" not in _clients:
        from alpaca.trading.client import TradingClient
        _clients["trading"] = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=_PAPER)
    return _clients["trading"]


def _data():
    """Lazily build & cache the alpaca-py market-data client."""
    require_alpaca()
    if "data" not in _clients:
        from alpaca.data.historical import StockHistoricalDataClient
        _clients["data"] = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    return _clients["data"]


def get_api():
    """The raw alpaca-py TradingClient (native methods: get_all_positions(), submit_order(), ...)."""
    return _trading()


def get_positions():
    """All open positions as alpaca-py Position objects."""
    return _trading().get_all_positions()


def get_account():
    """The alpaca-py Account object."""
    return _trading().get_account()


def list_recent_orders(status="closed", limit=100):
    """Recent orders as alpaca-py Order objects."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    st = {"closed": QueryOrderStatus.CLOSED, "open": QueryOrderStatus.OPEN,
          "all": QueryOrderStatus.ALL}.get(status, QueryOrderStatus.CLOSED)
    return _trading().get_orders(GetOrdersRequest(status=st, limit=limit))


def get_portfolio_history(period="all", timeframe="1D"):
    """Portfolio history object (has .timestamp and .equity lists)."""
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    return _trading().get_portfolio_history(GetPortfolioHistoryRequest(period=period, timeframe=timeframe))


def get_account_summary():
    """Get current account state."""
    account = get_account()
    positions = get_positions()

    pos_list = []
    for p in positions:
        pos_list.append({
            "symbol": p.symbol,
            "qty": p.qty,
            "side": "long" if float(p.qty) > 0 else "short",
            "entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc) * 100,
        })

    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value),
        "positions": pos_list,
    }


def place_experiment(symbol, direction, notional_amount, extended_hours=False):
    """
    Place a paper trade for a research experiment.

    Args:
        symbol: Stock ticker
        direction: "long" or "short"
        notional_amount: Dollar amount to invest
        extended_hours: If True, place a limit order for extended hours trading
            (pre-market / after-hours). Only limit orders work outside regular
            hours on Alpaca. time_in_force is forced to "day".

    Returns:
        dict with order details
    """
    if not symbol or symbol == "TBD":
        return {"success": False, "error": f"Cannot trade symbol '{symbol}' — resolve to a real ticker first"}

    side = "buy" if direction == "long" else "sell"

    # Duplicate position guard: refuse to open a second position in the same symbol.
    # Bug found 2026-04-14: SPY VIX>30 trade was doubled ($10K instead of $5K)
    # because both activate_vix_spy_trade.py and trade_loop.py placed orders.
    try:
        existing = {p.symbol: p for p in get_positions()}
        if symbol in existing and direction == "long":
            pos = existing[symbol]
            return {
                "success": False,
                "error": f"DUPLICATE GUARD: Already holding {pos.qty} shares of {symbol} "
                         f"(cost ${float(pos.cost_basis):,.0f}). Close existing position first."
            }
    except Exception:
        pass  # If we can't check, proceed cautiously

    # Validate position size against portfolio limits
    try:
        account = get_account()
        portfolio_value = float(account.portfolio_value)
        max_notional = portfolio_value * MAX_POSITION_PCT
        if notional_amount > max_notional:
            return {
                "success": False,
                "error": f"Notional ${notional_amount:,.0f} exceeds {MAX_POSITION_PCT*100:.0f}% "
                         f"of portfolio (max ${max_notional:,.0f})"
            }
    except Exception as e:
        return {"success": False, "error": f"Could not validate position size: {e}"}

    price = get_current_price(symbol)
    if price is None:
        return {"success": False, "error": f"Could not get price for {symbol}"}

    try:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side_enum = OrderSide.BUY if direction == "long" else OrderSide.SELL

        # Extended-hours orders must use limit orders with time_in_force=DAY.
        # Regular-hours orders use market orders (notional for longs, qty for shorts).
        if extended_hours:
            # Limit price slightly aggressive to improve fill probability:
            #   shorts: -0.1% (stay near bid); longs: +0.1% (stay near ask)
            limit_price = round(price * (0.999 if direction == "short" else 1.001), 2)
            qty = int(notional_amount / price)
            if qty < 1:
                return {"success": False, "error": f"Notional ${notional_amount} too small for {symbol} at ${price}"}
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=side_enum,
                time_in_force=TimeInForce.DAY, limit_price=limit_price, extended_hours=True,
            )
            print(f"[EXTENDED HOURS] Placing {side.upper()} limit order: {symbol} x{qty} @ ${limit_price:.2f} "
                  f"(last trade ${price:.2f})")
        else:
            # Alpaca only supports notional for buy-side market orders.
            # For short sells, compute qty from notional amount.
            if direction == "short":
                qty = int(notional_amount / price)
                if qty < 1:
                    return {"success": False, "error": f"Notional ${notional_amount} too small for {symbol} at ${price}"}
                req = MarketOrderRequest(symbol=symbol, qty=qty, side=side_enum, time_in_force=TimeInForce.DAY)
            else:
                req = MarketOrderRequest(symbol=symbol, notional=round(notional_amount, 2), side=side_enum, time_in_force=TimeInForce.DAY)

        order = _trading().submit_order(order_data=req)
        result = {
            "success": True,
            "order_id": order.id,
            "symbol": symbol,
            "side": side,
            "notional": notional_amount,
            "approx_qty": round(notional_amount / price, 4),
            "price_at_order": price,
            "extended_hours": extended_hours,
        }
        if extended_hours:
            result["limit_price"] = limit_price
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def close_position(symbol):
    """Close an entire position in a symbol."""
    try:
        _trading().close_position(symbol)
        return {"success": True, "symbol": symbol}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_current_price(symbol):
    """Get the current price for a symbol (latest trade)."""
    from alpaca.data.requests import StockLatestTradeRequest
    try:
        resp = _data().get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(resp[symbol].price)
    except Exception:
        return None


def _load_hypotheses():
    """Load hypotheses from SQLite database."""
    return _db.load_hypotheses()


def _update_peak_equity(current_equity):
    """Track peak portfolio equity for drawdown calculation. Uses SQLite kv_state."""
    data = _db.get_state("peak_equity") or {}
    peak = max(data.get("peak_equity", current_equity), current_equity)
    _db.set_state("peak_equity", {"peak_equity": peak, "updated": datetime.now().isoformat()})
    return peak


def check_stop_losses():
    """
    Check all active hypotheses for stop-loss, take-profit, and deadline violations.
    Closes positions that breach limits. Runs independently of the LLM agent.

    Returns list of actions taken.
    """
    hypotheses = _load_hypotheses()
    active = [h for h in hypotheses if h.get("status") == "active"]
    if not active:
        return []

    try:
        positions = {p.symbol: p for p in get_positions()}
        account = get_account()
        current_equity = float(account.equity)
    except Exception as e:
        return [{"action": "error", "message": f"Could not connect to Alpaca: {e}"}]

    # Portfolio-level drawdown check
    peak_equity = _update_peak_equity(current_equity)
    drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100 if peak_equity > 0 else 0

    actions = []
    now = datetime.now()

    for h in hypotheses:
        if h.get("status") != "active":
            continue

        symbol = h.get("expected_symbol")
        trade = h.get("trade", {})
        if not symbol or not trade:
            continue

        entry_price = trade.get("entry_price", 0)
        direction = h.get("expected_direction", "long")
        stop_loss_pct = trade.get("stop_loss_pct") or DEFAULT_STOP_LOSS_PCT
        take_profit_pct = trade.get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT)
        deadline = trade.get("deadline")

        # Get current position
        pos = positions.get(symbol)
        if not pos:
            actions.append({
                "action": "warning",
                "hypothesis_id": h["id"],
                "symbol": symbol,
                "message": f"No Alpaca position found for active hypothesis",
            })
            continue

        current_price = float(pos.current_price)
        unrealized_pct = float(pos.unrealized_plpc) * 100

        # For shorts, loss is positive price move
        if direction == "short":
            position_return_pct = -unrealized_pct
        else:
            position_return_pct = unrealized_pct

        reason = None

        # Stop-loss check (always active — stop_loss_pct is never None/0 after the guard above)
        if stop_loss_pct is not None and position_return_pct <= -stop_loss_pct:
            reason = f"STOP-LOSS: {symbol} down {position_return_pct:+.1f}% (limit: -{stop_loss_pct}%)"

        # Take-profit check
        elif take_profit_pct and position_return_pct >= take_profit_pct:
            reason = f"TAKE-PROFIT: {symbol} up {position_return_pct:+.1f}% (target: +{take_profit_pct}%)"

        # Deadline check
        elif deadline:
            try:
                deadline_dt = datetime.fromisoformat(deadline)
                # Bugfix 2026-06-08: `now` is naive but stored deadlines are
                # tz-aware (e.g. "...-04:00"). Comparing naive vs aware raises
                # TypeError, which was silently swallowed here — so deadline
                # closes NEVER fired (positions stuck open weeks past deadline).
                # Normalize `now` to the deadline's tzinfo before comparing.
                now_cmp = now
                if deadline_dt.tzinfo is not None:
                    now_cmp = datetime.now(deadline_dt.tzinfo)
                if now_cmp > deadline_dt:
                    reason = f"DEADLINE: {symbol} held past deadline ({deadline[:10]}), return {position_return_pct:+.1f}%"
            except (ValueError, TypeError):
                pass

        if reason:
            # Close the position
            result = close_position(symbol)
            if result.get("success"):
                # Mark hypothesis as completed
                h["status"] = "completed"
                # Compute abnormal return and direction_correct
                raw_ret = round(unrealized_pct, 2)
                spy_ret = None
                abnormal_ret = raw_ret
                try:
                    from tools.yfinance_utils import safe_download
                    spy_at_entry = trade.get("spy_at_entry")
                    if spy_at_entry:
                        _end = datetime.now().strftime('%Y-%m-%d')
                        _start = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
                        spy_data = safe_download("SPY", _start, _end)
                        if spy_data is not None and len(spy_data) > 0:
                            spy_now = float(spy_data["Close"].iloc[-1])
                            spy_ret = round((spy_now / spy_at_entry - 1) * 100, 2)
                            abnormal_ret = round(raw_ret - spy_ret, 2)
                except Exception as e:
                    print(f"[TRADE LOOP] WARNING: SPY fetch failed for {symbol}: {e}. Using raw return as abnormal.")

                # Direction correctness: must match expected direction AND exceed
                # minimum threshold (0.5%) to filter noise — consistent with
                # complete_hypothesis() in research.py
                MIN_DIRECTION_THRESHOLD = 0.5  # pct
                if direction == "long":
                    dir_matches = abnormal_ret > 0
                else:
                    dir_matches = abnormal_ret < 0
                dir_correct = dir_matches and abs(abnormal_ret) >= MIN_DIRECTION_THRESHOLD
                h["result"] = {
                    "exit_price": current_price,
                    "exit_time": now.isoformat(),
                    "raw_return_pct": raw_ret,
                    "spy_return_pct": spy_ret,
                    "abnormal_return_pct": abnormal_ret,
                    "exit_reason": reason,
                    "auto_closed": True,
                    "spy_at_entry": trade.get("spy_at_entry"),
                    "direction_correct": dir_correct,
                    "direction_matches_but_below_threshold": dir_matches and not dir_correct,
                }
                # Update pattern tracking for this signal
                try:
                    from research import _update_pattern
                    _update_pattern(h)
                except Exception:
                    pass
                # Save THIS hypothesis only (avoids bulk overwrite race condition)
                _db.save_hypothesis(h)
                actions.append({
                    "action": "closed",
                    "hypothesis_id": h["id"],
                    "symbol": symbol,
                    "reason": reason,
                    "return_pct": round(unrealized_pct, 2),
                })
            else:
                actions.append({
                    "action": "close_failed",
                    "hypothesis_id": h["id"],
                    "symbol": symbol,
                    "reason": reason,
                    "error": result.get("error"),
                })

    # Portfolio drawdown alert
    if drawdown_pct > MAX_PORTFOLIO_DRAWDOWN_PCT:
        actions.append({
            "action": "drawdown_alert",
            "message": f"Portfolio drawdown {drawdown_pct:.1f}% exceeds {MAX_PORTFOLIO_DRAWDOWN_PCT}% limit. "
                       f"Peak: ${peak_equity:,.0f}, Current: ${current_equity:,.0f}. "
                       f"New trades should be halted.",
            "drawdown_pct": round(drawdown_pct, 1),
        })

    return actions


def check_portfolio_drawdown():
    """Check if portfolio drawdown exceeds the safety limit. Returns True if safe to trade."""
    try:
        account = get_account()
        current_equity = float(account.equity)
        peak = _update_peak_equity(current_equity)
        drawdown = ((peak - current_equity) / peak) * 100 if peak > 0 else 0
        return {
            "safe_to_trade": drawdown < MAX_PORTFOLIO_DRAWDOWN_PCT,
            "current_equity": current_equity,
            "peak_equity": peak,
            "drawdown_pct": round(drawdown, 1),
            "limit_pct": MAX_PORTFOLIO_DRAWDOWN_PCT,
        }
    except Exception as e:
        return {"safe_to_trade": False, "error": str(e)}


if __name__ == "__main__":
    # Can be run directly: python trader.py --check-stops
    if len(sys.argv) > 1 and sys.argv[1] == "--check-stops":
        actions = check_stop_losses()
        if actions:
            for a in actions:
                print(f"[{a['action']}] {a.get('symbol', '')} {a.get('reason', a.get('message', ''))}")
        else:
            print("No stop-loss triggers.")
