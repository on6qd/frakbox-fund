"""tools package initialization.

CRITICAL INFRASTRUCTURE SHIM — do not remove.

yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
throughout its internals (base.py / multi.py / scrapers/history.py / data.py).
The Chrome TLS fingerprint fails the egress proxy handshake in the cloud
execution environment with::

    curl: (35) TLS connect error: error:00000000:invalid library (0):
    OPENSSL_internal:invalid library (0)

The Safari fingerprint negotiates cleanly (verified: chrome -> SSLError,
safari -> HTTP 200). This module monkeypatches ``curl_cffi.requests.Session``
so that the *exact* value ``impersonate="chrome"`` is rewritten to
``"safari"``. Versioned profiles (e.g. ``chrome120``) are left untouched.

The patch is idempotent and triggers automatically on any ``from tools ...``
import, so every code path that reaches yfinance through this package is
covered. ``yfinance_utils`` imports the package to self-trigger; modules that
import yfinance directly (e.g. ``nt_filing_scanner``) get covered as long as
they live in / import from the ``tools`` package.

History: this fix was described as "committed" in multiple prior sessions but
was never actually persisted to git, so it was lost on every fresh clone
(recurring data_access friction #1, 137x). See knowledge entries
``yfinance_curl_cffi_chrome_to_safari_proxy_patch_2026_06_26`` and
``yfinance_curl_cffi_shim_committed_tools_init_2026_06_27``. Guarded by
``tests/test_curl_cffi_shim.py``.
"""


def _apply_curl_cffi_safari_shim():
    """Rewrite curl_cffi Session impersonate='chrome' -> 'safari'.

    Returns True if the patch was applied (or already applied), False if
    curl_cffi is unavailable. Idempotent: safe to call repeatedly.
    """
    try:
        from curl_cffi import requests as _creq
    except Exception:
        return False

    Session = getattr(_creq, "Session", None)
    if Session is None:
        return False

    # Already patched? (idempotent guard)
    if getattr(Session.__init__, "_safari_shim_applied", False):
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim_applied = True
    Session.__init__ = _patched_init
    return True


# Apply on package import so any `from tools ...` self-triggers the fix.
_apply_curl_cffi_safari_shim()
