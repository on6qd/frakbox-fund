"""Guard test for the curl_cffi chrome->safari TLS shim in tools/__init__.py.

The shim is REQUIRED for yfinance to fetch behind the egress proxy (Chrome TLS
fingerprint fails the proxy handshake with curl error 35; Safari negotiates).
This fix has been silently lost on multiple fresh clones; this test fails loudly
if the shim ever regresses.

Run: python -m pytest tests/test_curl_cffi_shim.py  (or execute directly).
"""

import os
import sys

# Ensure the repo root is importable when this file is run directly (not via
# pytest from the repo root), so ``import tools`` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_shim_rewrites_bare_chrome_to_safari():
    import tools  # noqa: F401  (installs shim on import)
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome")
    assert s.impersonate == "safari", (
        "curl_cffi shim did not rewrite impersonate='chrome' to 'safari' — "
        "tools/__init__.py shim missing or broken; yfinance fetches will fail."
    )


def test_shim_leaves_versioned_chrome_untouched():
    import tools  # noqa: F401
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome124")
    assert s.impersonate == "chrome124", (
        "shim must only rewrite the bare 'chrome' value, not versioned profiles."
    )


def test_shim_is_idempotent():
    import tools

    # Re-running the installer must not double-wrap or raise.
    assert tools._install_curl_cffi_safari_shim() is True
    import curl_cffi.requests as ccr

    assert ccr.Session(impersonate="chrome").impersonate == "safari"


if __name__ == "__main__":
    test_shim_rewrites_bare_chrome_to_safari()
    test_shim_leaves_versioned_chrome_untouched()
    test_shim_is_idempotent()
    print("All curl_cffi shim guard tests passed.")
