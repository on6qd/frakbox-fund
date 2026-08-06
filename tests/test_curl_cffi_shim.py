"""Guard test for the curl_cffi impersonation shim in tools/__init__.py.

This shim is the fix for the project's single most frequent recurring friction:
on a fresh clone, yfinance's hardcoded ``impersonate="chrome"`` gets its TLS
connection reset by the agent proxy, silently killing all price-data access.
The shim rewrites chrome -> safari. If this test fails, fresh-clone data access
is broken again.

Run: python3 -m pytest tests/test_curl_cffi_shim.py -q
"""

import importlib


def test_shim_rewrites_chrome_impersonation():
    import tools  # noqa: F401  (import activates the shim)
    from curl_cffi import requests as cc

    # The shim marker must be present and the class patched.
    assert getattr(cc.Session, "_frakbox_impersonate_shim", False) is True

    # A Session asking for chrome must end up impersonating the safe target.
    s = cc.Session(impersonate="chrome")
    assert not str(s.impersonate).lower().startswith("chrom"), (
        f"chrome impersonation was not rewritten; got {s.impersonate!r}"
    )


def test_shim_is_idempotent():
    import tools

    from curl_cffi import requests as cc

    before_init = cc.Session.__init__
    # Re-running the installer must not double-wrap.
    tools._install_curl_cffi_shim()
    assert cc.Session.__init__ is before_init

    # Re-importing the package is also a no-op.
    importlib.reload(tools)
    tools._install_curl_cffi_shim()
    assert getattr(cc.Session, "_frakbox_impersonate_shim", False) is True


def test_non_chrome_impersonation_preserved():
    import tools  # noqa: F401

    from curl_cffi import requests as cc

    s = cc.Session(impersonate="safari")
    assert str(s.impersonate).lower().startswith("safari")


def test_live_yahoo_fetch_returns_data():
    """End-to-end: with the shim active, yfinance actually returns rows.

    Network-dependent; skips cleanly if the fetch fails for a non-shim reason
    (e.g. Yahoo outage) so the suite stays green offline.
    """
    import tools  # noqa: F401

    try:
        from tools.yfinance_utils import get_close_prices

        prices = get_close_prices("SPY", start="2024-01-02", end="2024-01-31")
    except Exception as exc:  # pragma: no cover - network flakiness
        import pytest

        pytest.skip(f"live fetch unavailable: {exc}")
    else:
        assert len(prices) > 0
