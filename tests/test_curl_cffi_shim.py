"""Guard test for the curl_cffi chrome->safari shim in tools/__init__.py.

This is the #1 recurring data_access friction: yfinance hardcodes
impersonate="chrome", whose TLS fingerprint the egress proxy rejects (curl 35).
tools/__init__.py monkeypatches curl_cffi to rewrite chrome -> safari. If this
test fails, every yfinance price fetch will silently die in web sessions.

Run: python3 -m pytest tests/test_curl_cffi_shim.py -q
or:  python3 tests/test_curl_cffi_shim.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_init_file_present():
    import tools
    path = os.path.join(os.path.dirname(tools.__file__), "__init__.py")
    assert os.path.getsize(path) > 0, "tools/__init__.py must contain the shim"


def test_shim_flag_installed():
    import tools  # arms the shim
    import curl_cffi.requests as ccr
    assert getattr(ccr.Session.__init__, "_chrome_safari_shim", False), \
        "Session.__init__ not patched"
    assert getattr(ccr.AsyncSession.__init__, "_chrome_safari_shim", False), \
        "AsyncSession.__init__ not patched"


def test_chrome_rewritten_versioned_untouched():
    import tools
    assert tools._rewrite("chrome") == tools._TARGET
    assert tools._rewrite("chrome124") == "chrome124"
    assert tools._rewrite("safari15_5") == "safari15_5"


def test_end_to_end_price_fetch():
    import tools  # noqa
    from tools.yfinance_utils import get_close_prices
    s = get_close_prices("SPY", "2026-08-01", "2026-08-15")
    assert s is not None and len(s) > 0, "live SPY fetch returned no rows"


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
                passed += 1
            except Exception as e:
                print(f"FAIL {name}: {e}")
    print(f"\n{passed} passed")
