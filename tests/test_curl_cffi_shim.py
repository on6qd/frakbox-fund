"""Guard test for the recurring curl_cffi chrome->safari impersonation shim.

This test fails loudly if tools/__init__.py ever loses the monkeypatch that
rewrites curl_cffi impersonate="chrome" -> "safari". Without it, every yfinance
fetch fails behind the egress proxy (curl 35 TLS / SSLError). See the knowledge
entry yfinance_curl_cffi_chrome_to_safari_proxy_patch_2026_06_26.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_chrome_impersonate_rewritten_to_safari():
    import tools  # noqa: F401  applies the shim on import
    import curl_cffi.requests as ccr

    sess = ccr.Session(impersonate="chrome")
    # curl_cffi stores the negotiated impersonation on the session.
    val = getattr(sess, "impersonate", None)
    assert val == "safari", f"expected chrome->safari rewrite, got {val!r}"


def test_versioned_chrome_profile_untouched():
    import tools  # noqa: F401
    import curl_cffi.requests as ccr

    sess = ccr.Session(impersonate="chrome124")
    val = getattr(sess, "impersonate", None)
    assert val == "chrome124", f"versioned profile must be untouched, got {val!r}"


if __name__ == "__main__":
    test_chrome_impersonate_rewritten_to_safari()
    test_versioned_chrome_profile_untouched()
    print("curl_cffi shim guard: PASS")
