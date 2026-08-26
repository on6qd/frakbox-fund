"""
tools package initializer.

PERMANENT FIX for the recurring data_access friction (documented ~19x in the
friction log): every yfinance fetch fails in fresh cloud clones with

    SSLError('Failed to perform, curl: (35) Recv failure: Connection reset by peer')

Root cause: yfinance (base.py / multi.py / scrapers/history.py / data.py) hardcodes

    requests.Session(impersonate="chrome")

The Chrome TLS fingerprint is rejected by the egress proxy handshake, while the
Safari fingerprint negotiates cleanly (verified: chrome -> curl 35, safari -> HTTP 200).

Fix: monkeypatch curl_cffi's Session.__init__ so that an impersonate value of exactly
"chrome" is rewritten to "safari". The patch is:
  - idempotent (guarded so it is only applied once, even on repeated imports),
  - narrow (only the exact string "chrome" is rewritten; versioned Chrome profiles
    such as "chrome124" and every other impersonation target are left untouched),
  - self-triggering (importing anything under the `tools` package runs this, and
    tools/yfinance_utils.py imports the package so the canonical fetch path is covered).

This file MUST stay committed. Prior sessions kept losing the fix because
tools/__init__.py was never committed to a branch that merged to main.
Guarded by tests/test_curl_cffi_shim.py::test_curl_cffi_shim_applied.
"""

from __future__ import annotations


def _rewrite_impersonate(kwargs: dict) -> dict:
    """Rewrite an impersonate kwarg of exactly 'chrome' to 'safari'.

    Narrow by design: only the exact string 'chrome' is rewritten. Versioned
    profiles ('chrome124', ...) and every other impersonation target pass through
    untouched. Mutates and returns kwargs for convenience.
    """
    if kwargs.get("impersonate") == "chrome":
        kwargs["impersonate"] = "safari"
    return kwargs


def _apply_curl_cffi_safari_shim() -> bool:
    """Rewrite curl_cffi impersonate='chrome' -> 'safari'. Returns True if applied."""
    try:
        from curl_cffi import requests as _cc_requests
    except Exception:
        # curl_cffi not installed / import failure: nothing to patch. yfinance may
        # be using a different HTTP backend; let it proceed unpatched.
        return False

    Session = _cc_requests.Session

    # Idempotency guard: never double-wrap.
    if getattr(Session.__init__, "_safari_shim_applied", False):
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        _rewrite_impersonate(kwargs)
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim_applied = True  # type: ignore[attr-defined]
    Session.__init__ = _patched_init  # type: ignore[assignment]
    return True


# Apply on package import so any `from tools ...` / `import tools` self-triggers it.
_SHIM_APPLIED = _apply_curl_cffi_safari_shim()
