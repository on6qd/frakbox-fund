"""
Guard test for the curl_cffi chrome->safari impersonation shim.

Fresh clones have repeatedly lost tools/__init__.py, silently breaking every
yfinance price fetch (the Chrome TLS fingerprint fails the egress proxy). This
test fails loudly if the shim ever stops being applied on package import, so
the loss cannot go unnoticed again.

Run: python3 -m pytest tests/test_curl_cffi_shim.py -q
     (or:  python3 tests/test_curl_cffi_shim.py)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import curl_cffi.requests as cr

# Importing the tools package must apply the shim as a side effect.
import tools  # noqa: F401


def test_shim_is_applied():
    """tools/__init__.py must mark curl_cffi Session.__init__ as patched."""
    assert getattr(cr.Session.__init__, "_chrome_to_safari_shim", False), (
        "curl_cffi chrome->safari shim is NOT applied — tools/__init__.py is "
        "missing or not imported. yfinance price fetches will fail against the "
        "egress proxy. See tools/__init__.py."
    )


def test_chrome_is_rewritten_to_safari():
    """A Session built with impersonate='chrome' must actually use safari."""
    s = cr.Session(impersonate="chrome")
    # curl_cffi stores the resolved impersonate target on the session.
    resolved = getattr(s, "impersonate", None)
    assert resolved == "safari", (
        f"Expected impersonate rewritten chrome->safari, got {resolved!r}"
    )


def test_versioned_chrome_profile_untouched():
    """Only the bare 'chrome' alias is remapped; versioned profiles survive."""
    s = cr.Session(impersonate="chrome124")
    resolved = getattr(s, "impersonate", None)
    assert resolved == "chrome124", (
        f"Versioned chrome profile should be untouched, got {resolved!r}"
    )


if __name__ == "__main__":
    test_shim_is_applied()
    test_chrome_is_rewritten_to_safari()
    test_versioned_chrome_profile_untouched()
    print("OK: curl_cffi chrome->safari shim verified")
