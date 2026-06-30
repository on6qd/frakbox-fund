"""Guard test for the curl_cffi -> safari impersonation shim in tools/__init__.py.

yfinance 1.2.0 hardcodes curl_cffi Session(impersonate="chrome"), which fails the
TLS handshake through the Claude Code agent proxy ("invalid library" OpenSSL
error). The shim in tools/__init__.py rewrites every curl_cffi Session to safari
and points verify at the proxy CA bundle. This file has been silently absent on
several fresh clones, re-breaking every price fetch -- this test fails loudly when
that happens so the regression is caught before a session burns calls debugging it.

Run: python3 -m pytest tests/test_curl_cffi_shim.py -q
"""

import importlib


def test_shim_rewrites_chrome_to_safari():
    import tools  # noqa: F401  (import installs the shim)
    from curl_cffi import requests as creq

    assert getattr(creq.Session, "_frakbox_safari_shim", False), (
        "tools/__init__.py did not install the curl_cffi safari shim -- "
        "the file is likely missing or empty on this clone."
    )

    # An explicit chrome request must be rewritten to safari.
    s = creq.Session(impersonate="chrome")
    assert s.impersonate == "safari", (
        f"expected impersonate rewritten to 'safari', got {s.impersonate!r}"
    )

    # A bare Session (yfinance's default path) must also land on safari.
    s2 = creq.Session()
    assert s2.impersonate == "safari"


def test_yfinance_imports_clean():
    # yfinance must import after the shim without re-pinning a chrome session.
    yf = importlib.import_module("yfinance")
    assert yf.__version__
