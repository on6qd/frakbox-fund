"""
Guard test for the recurring curl_cffi impersonation shim (tools/__init__.py).

yfinance hardcodes curl_cffi Session(impersonate="chrome"), which the agent
proxy resets in this environment. The shim rewrites chrome -> safari. If this
test fails on a fresh clone, the shim is missing again and every yfinance-backed
data task will silently break.
"""

import importlib


def test_shim_rewrites_chrome_to_safari():
    import tools  # noqa: F401  (import installs the shim)
    from curl_cffi import requests as cc

    assert getattr(cc.Session, "_frakbox_impersonation_shimmed", False), \
        "curl_cffi Session was not patched by tools/__init__.py"

    s = cc.Session(impersonate="chrome")
    assert getattr(s, "impersonate", "safari") != "chrome"


def test_shim_default_impersonate_is_safe():
    import tools  # noqa: F401
    from curl_cffi import requests as cc

    s = cc.Session()  # no impersonate -> shim should set safari
    assert getattr(s, "impersonate", None) == "safari"


def test_yfinance_download_live():
    """Live smoke test: SPY must return rows through the safari fingerprint."""
    from tools.yfinance_utils import safe_download

    df = safe_download("SPY", start="2026-07-01", end="2026-08-08")
    assert df is not None and len(df) > 0, "yfinance returned no rows — shim broken?"
    assert "Close" in df.columns
