"""
tools package init — curl_cffi chrome->safari impersonation shim.

RECURRING FRICTION FIX (data_access, 137x). yfinance 1.2.0 hardcodes
curl_cffi requests.Session(impersonate="chrome") across its base/multi/
scrapers. The Chrome TLS fingerprint fails the egress proxy handshake
(curl 35: OPENSSL_internal invalid library), while Safari negotiates
cleanly (verified: chrome -> SSLError, safari -> HTTP 200).

Fix: monkeypatch curl_cffi.requests.Session.__init__ to rewrite the exact
impersonate value "chrome" -> "safari". Idempotent, and it only touches the
bare "chrome" profile — versioned profiles ("chrome124", etc.) pass through
untouched.

This runs on ANY import from the tools package. tools/yfinance_utils.py is
imported by nearly every code path, so importing it self-triggers the shim.
Modules that import yfinance directly (e.g. nt_filing_scanner.py) must
`import tools` first to arm it.

HISTORY: this fix was recorded "committed" several times but was repeatedly
lost because tools/__init__.py was never actually committed to the working
branch. A fresh clone of a branch cut from default silently drops it and
every price fetch breaks. It MUST stay committed on the active branch.
Guarded by tests/test_curl_cffi_shim.py.
"""

_SHIM_APPLIED = False


def _apply_curl_cffi_safari_shim():
    """Rewrite curl_cffi's bare 'chrome' impersonation to 'safari'. Idempotent."""
    global _SHIM_APPLIED
    if _SHIM_APPLIED:
        return
    try:
        import curl_cffi.requests.session as _cc_session
    except Exception:
        # curl_cffi not installed / unexpected layout — nothing to patch.
        return

    _orig_init = _cc_session.Session.__init__

    # Guard against double-wrapping if this module is re-imported/reloaded.
    if getattr(_orig_init, "_safari_shim", False):
        _SHIM_APPLIED = True
        return

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim = True
    _cc_session.Session.__init__ = _patched_init
    _SHIM_APPLIED = True


_apply_curl_cffi_safari_shim()
