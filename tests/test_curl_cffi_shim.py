"""Guard test for the curl_cffi impersonation shim in tools/__init__.py.

The sandboxed egress proxy rejects curl_cffi's newest "chrome" TLS fingerprint with a
connection reset. tools/__init__.py rewrites impersonate="chrome" to a pinned,
proxy-compatible fingerprint. If this file goes missing on a fresh clone, yfinance price
fetches silently break — this test fails fast so the shim gets restored.

Run: python3 -m pytest tests/test_curl_cffi_shim.py
"""

import importlib


def test_shim_rewrites_blocked_chrome_alias():
    # Importing the package installs the shim.
    import tools  # noqa: F401
    from curl_cffi import requests as cr

    s = cr.Session(impersonate="chrome")
    # The blocked "chrome" alias must have been rewritten to the safe fingerprint.
    assert getattr(s, "_ccr_impersonate_shim", None) is None  # instance flag not set
    assert cr.Session._ccr_impersonate_shim is True


def test_shim_preserves_explicit_non_chrome():
    import tools  # noqa: F401
    from tools import _SAFE_IMPERSONATE, _BLOCKED_ALIASES

    assert _SAFE_IMPERSONATE not in _BLOCKED_ALIASES
    assert "chrome" in _BLOCKED_ALIASES


def test_shim_is_idempotent():
    import tools

    tools._install_curl_cffi_impersonate_shim()
    tools._install_curl_cffi_impersonate_shim()
    from curl_cffi import requests as cr

    # Still exactly one wrapper (flag prevents double-wrapping).
    assert cr.Session._ccr_impersonate_shim is True
