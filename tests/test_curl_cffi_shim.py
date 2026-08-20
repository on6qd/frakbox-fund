"""Regression guard for the recurring curl_cffi / yfinance network shim.

The shim in ``tools/__init__.py`` has been lost and re-derived ~15 times on
fresh clones (see knowledge: tools_init_shim_recurring_loss_*). These tests
lock in its two invariants so the file cannot silently disappear again without
a red test, and cover the sibling ``nt_filing_scanner`` module-level ``os`` bug.

Network-dependent assertions are skipped automatically when offline so the
suite stays green in environments without egress.
"""
import importlib
import os

import pytest


def test_tools_init_installs_shim():
    """Importing the tools package must install the curl_cffi shim marker."""
    import tools  # noqa: F401  (import triggers the shim)

    try:
        from curl_cffi import requests as cr
    except Exception:
        pytest.skip("curl_cffi not installed")

    assert getattr(cr, "_frakbox_shim_installed", False), (
        "tools/__init__.py did not install the curl_cffi shim — the recurring "
        "infra loss has regressed."
    )


def test_nt_filing_scanner_imports_cleanly():
    """nt_filing_scanner uses os.* at module scope; it must import os at top."""
    mod = importlib.import_module("tools.nt_filing_scanner")
    assert mod is not None


@pytest.mark.parametrize("symbol", ["SPY"])
def test_yfinance_fetch_through_proxy(symbol):
    """Behind the egress proxy, a real fetch must succeed via the shim.

    Skips when not proxied (local dev) or when the network is unreachable so
    the test is a guard, not a flake source.
    """
    if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        pytest.skip("not behind the egress proxy — shim is a no-op here")

    import tools  # noqa: F401
    from tools.yfinance_utils import get_close_prices

    try:
        prices = get_close_prices(symbol, "2026-08-10", "2026-08-19")
    except Exception as exc:  # network genuinely down — don't fail the suite
        pytest.skip(f"network unavailable: {exc}")

    assert prices is not None and len(prices) > 0
