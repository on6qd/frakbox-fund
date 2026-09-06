"""Guard test for the curl_cffi chrome->safari impersonation shim.

The shim (tools/__init__.py) is the fix for recurring friction #1: yfinance's
hardcoded ``impersonate="chrome"`` fails the egress proxy TLS handshake. If this
file starts failing, yfinance is (or is about to be) broken again on a fresh
clone. Run with: python -m pytest tests/test_curl_cffi_shim.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_shim_installed():
    import tools

    assert tools._SHIM_INSTALLED is True, "curl_cffi shim did not install"


def test_chrome_is_rewritten():
    import tools
    import curl_cffi.requests as ccr

    sess = ccr.Session(impersonate="chrome")
    try:
        # After the shim, a session asked to impersonate bare "chrome" must not
        # keep that value — it should be the safari target.
        assert getattr(sess, "impersonate", tools._TARGET) != "chrome"
    finally:
        sess.close()


def test_versioned_profiles_untouched():
    import tools  # noqa: F401
    import curl_cffi.requests as ccr

    # An explicitly versioned profile must pass through unchanged.
    sess = ccr.Session(impersonate="safari17_0")
    try:
        assert getattr(sess, "impersonate", "safari17_0") == "safari17_0"
    finally:
        sess.close()


def test_idempotent():
    import tools

    # Re-installing must be a no-op and still report installed.
    assert tools._install_curl_cffi_shim() is True


if __name__ == "__main__":
    test_shim_installed()
    test_chrome_is_rewritten()
    test_versioned_profiles_untouched()
    test_idempotent()
    print("all shim guard tests PASS")
