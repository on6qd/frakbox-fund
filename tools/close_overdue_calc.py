import sys; sys.path.insert(0,".."); sys.path.insert(0,"."); from yfinance_utils import get_close_prices
import pandas as pd

def px(sym, start, end):
    s = get_close_prices(sym, start=start, end=end)
    return s

# SPY long: entry 2026-03-30, deadline 2026-04-27
spy = get_close_prices("SPY", start="2026-03-27", end="2026-06-08")
print("=== SPY (b63a0168 long) entry 639.885 on 2026-03-30 ===")
for d in ["2026-03-30","2026-04-27","2026-06-05"]:
    sub = spy[spy.index <= d]
    if len(sub): print(d, "->", round(float(sub.iloc[-1]),2), "(last avail", sub.index[-1].date(), ")")

# DRVN short: entry 2026-04-22 @12.28, deadline 2026-05-06
drvn = get_close_prices("DRVN", start="2026-04-20", end="2026-06-08")
spy2 = spy
print("=== DRVN (995a7465 short) entry 12.28 on 2026-04-22 ===")
for d in ["2026-04-22","2026-05-06","2026-06-05"]:
    sub = drvn[drvn.index <= d]
    if len(sub): print(d, "->", round(float(sub.iloc[-1]),3), "(", sub.index[-1].date(), ")")
print("=== SPY for DRVN benchmark window ===")
for d in ["2026-04-22","2026-05-06","2026-06-05"]:
    sub = spy[spy.index <= d]
    if len(sub): print(d, "->", round(float(sub.iloc[-1]),2))
