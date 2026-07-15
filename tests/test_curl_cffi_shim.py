"""Guard for the recurring curl_cffi chrome->safari shim (tools/__init__.py).

yfinance 1.2.0 hardcodes curl_cffi Session(impersonate="chrome"), whose TLS
fingerprint the cloud egress proxy rejects (curl 35). The shim in
tools/__init__.py rewrites chrome -> safari on import. This test fails loudly
if the shim is lost again on a fresh clone, so we catch it before every
yfinance fetch silently breaks.

Run: python3 -m pytest tests/test_curl_cffi_shim.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_shim_applied_flag():
    import tools
    assert tools._SHIM_APPLIED is True


def test_chrome_rewritten_to_safari():
    import tools
    assert tools._rewrite_impersonate("chrome") == "safari"
    assert tools._rewrite_impersonate("chrome131") == "safari"
    # Non-chrome values pass through unchanged.
    assert tools._rewrite_impersonate("safari") == "safari"
    assert tools._rewrite_impersonate("safari17_0") == "safari17_0"
    assert tools._rewrite_impersonate(None) is None


def test_curl_cffi_session_patched():
    import tools  # noqa: F401  (triggers the shim)
    from curl_cffi import requests as ccr
    assert getattr(ccr.Session, "_frakbox_chrome_to_safari", False) is True
    s = ccr.Session(impersonate="chrome")
    # yfinance passes impersonate as a constructor kwarg; after the shim the
    # session must be built with the Safari fingerprint, not Chrome.
    assert "chrome" not in str(getattr(s, "impersonate", "")).lower()


if __name__ == "__main__":
    test_shim_applied_flag()
    test_chrome_rewritten_to_safari()
    test_curl_cffi_session_patched()
    print("all curl_cffi shim guards passed")
