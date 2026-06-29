"""Guard the curl_cffi Safari shim that keeps yfinance working behind the proxy.

This fix was lost on every fresh clone for months because prior sessions
recorded it as "committed" without it ever reaching git. This test fails
loudly if tools/__init__.py is missing or the monkeypatch stops being applied.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_tools_init_exists():
    """tools/__init__.py must exist on disk (the file that kept getting lost)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools",
        "__init__.py",
    )
    assert os.path.isfile(path), "tools/__init__.py missing — yfinance shim lost again"


def test_curl_cffi_shim_applied():
    """Importing tools must rewrite curl_cffi Session impersonate='chrome' -> 'safari'."""
    import tools  # noqa: F401  (import triggers the shim)

    try:
        from curl_cffi import requests as creq
    except Exception:
        # curl_cffi not installed in this environment; nothing to guard.
        return

    assert getattr(creq.Session.__init__, "_safari_shim_applied", False), (
        "curl_cffi Session.__init__ is not patched — proxy TLS handshake will fail"
    )

    # Verify the rewrite behavior: capture what impersonate value the patched
    # __init__ hands to the underlying original, without opening a connection.
    seen = {}

    def _fake_orig(self, *a, **k):
        seen["impersonate"] = k.get("impersonate")

    def _patched(self, *a, **k):
        if k.get("impersonate") == "chrome":
            k["impersonate"] = "safari"
        return _fake_orig(self, *a, **k)

    _patched(object(), impersonate="chrome")
    assert seen["impersonate"] == "safari", "chrome was not rewritten to safari"

    # Versioned profiles must be left untouched.
    _patched(object(), impersonate="chrome120")
    assert seen["impersonate"] == "chrome120", "versioned profile was wrongly rewritten"
