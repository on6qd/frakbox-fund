"""Guard test for the curl_cffi chrome->safari impersonation shim.

The shim lives in tools/__init__.py and fixes the recurring data_access
friction (curl 35 TLS handshake failure behind the egress proxy). If this
test regresses, fresh clones lose all yfinance data access.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_shim_rewrites_chrome_to_safari():
    import tools  # noqa: F401  (importing installs the shim)
    from curl_cffi import requests

    s = requests.Session(impersonate="chrome")
    assert s.impersonate == "safari", (
        f"shim did not rewrite chrome->safari (got {s.impersonate!r})"
    )


def test_shim_leaves_versioned_profiles_untouched():
    import tools  # noqa: F401
    from curl_cffi import requests

    s = requests.Session(impersonate="safari")
    assert s.impersonate == "safari"


def test_shim_is_idempotent():
    import importlib
    import tools

    importlib.reload(tools)
    from curl_cffi import requests

    s = requests.Session(impersonate="chrome")
    assert s.impersonate == "safari"


if __name__ == "__main__":
    test_shim_rewrites_chrome_to_safari()
    test_shim_leaves_versioned_profiles_untouched()
    test_shim_is_idempotent()
    print("PASS: curl_cffi shim guard")
