"""Guard for the recurring curl_cffi chrome->safari shim in tools/__init__.py.

yfinance 1.2.0 constructs curl_cffi Session(impersonate="chrome"), whose TLS
fingerprint the cloud egress proxy rejects (curl 35). tools/__init__.py rewrites
that to "safari". If this test fails on a fresh clone, tools/__init__.py is
missing/again unmerged — recover it before running any price fetch.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_curl_cffi_shim_applied():
    import tools  # noqa: F401 -- triggers the monkeypatch
    import curl_cffi.requests as ccr

    assert getattr(ccr.Session, "_frakbox_safari_shim", False), (
        "tools/__init__.py curl_cffi shim not applied — fresh clone likely "
        "lost tools/__init__.py (must be merged to main to persist)"
    )


def test_shim_rewrites_chrome_to_safari():
    import tools  # noqa: F401
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome")
    # curl_cffi stores the negotiated impersonate on the session
    impersonate = getattr(s, "impersonate", None) or getattr(s, "_impersonate", None)
    assert impersonate == "safari", f"expected safari, got {impersonate!r}"


def test_shim_leaves_versioned_chrome_untouched():
    import tools  # noqa: F401
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome124")
    impersonate = getattr(s, "impersonate", None) or getattr(s, "_impersonate", None)
    assert impersonate == "chrome124", f"versioned profile altered: {impersonate!r}"


if __name__ == "__main__":
    test_curl_cffi_shim_applied()
    test_shim_rewrites_chrome_to_safari()
    test_shim_leaves_versioned_chrome_untouched()
    print("PASS: curl_cffi shim guards")
