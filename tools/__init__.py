"""tools package init.

PERMANENT FIX for the recurring data_access friction (see knowledge base
yfinance_curl_cffi_chrome_to_safari_proxy_patch_2026_06_26).

yfinance pins curl_cffi requests.Session(impersonate="chrome") across its
internals. The Chrome TLS fingerprint fails the egress proxy handshake
(curl 35 "OPENSSL_internal invalid library"), while Safari negotiates
cleanly (verified: chrome -> SSLError, safari -> HTTP 200).

We monkeypatch curl_cffi.requests.Session.__init__ to rewrite the exact
impersonate value "chrome" -> "safari". The patch is idempotent and only
touches the bare "chrome" token; versioned profiles (e.g. "chrome124")
are left untouched. Importing this package self-triggers the patch, so any
module that does `import tools...` or `from tools import ...` (and
yfinance_utils, which imports the package explicitly) gets the fix.
"""

def _patch_curl_cffi_impersonation():
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        return

    Session = getattr(_ccr, "Session", None)
    if Session is None:
        return

    if getattr(Session, "_frakbox_safari_patch", False):
        return

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    Session.__init__ = _patched_init
    Session._frakbox_safari_patch = True


_patch_curl_cffi_impersonation()
