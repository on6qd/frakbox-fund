"""Guard test: the curl_cffi chrome->safari shim must stay applied.

This is the permanent regression guard for the ~19x-recurring data_access
friction where fresh clones lost tools/__init__.py and every yfinance fetch
failed with curl 35 (Chrome TLS fingerprint rejected by the egress proxy).

Run: python -m pytest tests/test_curl_cffi_shim.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_curl_cffi_shim_applied():
    """Importing tools patches curl_cffi so impersonate='chrome' -> 'safari'."""
    import tools  # noqa: F401

    assert tools._SHIM_APPLIED is True, "tools/__init__.py shim did not apply"

    from curl_cffi import requests

    assert getattr(requests.Session.__init__, "_safari_shim_applied", False), (
        "curl_cffi Session.__init__ is not the shimmed version"
    )


def test_rewrite_only_exact_chrome():
    """Only the exact string 'chrome' is rewritten; other targets pass through."""
    import tools

    assert tools._rewrite_impersonate({"impersonate": "chrome"})["impersonate"] == "safari"
    # Versioned Chrome profiles and other browsers are left untouched.
    assert tools._rewrite_impersonate({"impersonate": "chrome124"})["impersonate"] == "chrome124"
    assert tools._rewrite_impersonate({"impersonate": "safari"})["impersonate"] == "safari"
    assert tools._rewrite_impersonate({"impersonate": "firefox"})["impersonate"] == "firefox"
    # No impersonate kwarg at all: unchanged, no crash.
    assert "impersonate" not in tools._rewrite_impersonate({})
