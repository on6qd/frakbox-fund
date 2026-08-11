"""tools package init.

RECURRING FRICTION FIX #1 (curl_cffi chrome->safari impersonation shim).

yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
throughout (base.py / multi.py / scrapers/history.py / data.py). The Chrome TLS
fingerprint fails the cloud egress proxy handshake (curl error 35,
OPENSSL_internal invalid library), while the Safari fingerprint negotiates
cleanly (verified: chrome -> SSLError, safari -> HTTP 200).

Fix: monkeypatch ``curl_cffi.requests.Session.__init__`` so that a request for
``impersonate == "chrome"`` (the EXACT bare value only) is rewritten to
"safari". Versioned profiles (e.g. "chrome124") are left untouched. The patch
is idempotent and applies as soon as anything under ``tools`` is imported,
which covers the canonical data path (tools/yfinance_utils.py imports this
package on first use).

This file is lost on every fresh clone until it lands on main; keep it here.
"""

_SAFARI_PROFILE = "safari"


def _install_curl_cffi_impersonation_shim():
    try:
        import curl_cffi.requests as _cr
    except Exception:
        return False

    Session = getattr(_cr, "Session", None)
    if Session is None:
        return False

    if getattr(Session, "_frakbox_impersonation_shim", False):
        return True  # idempotent

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = _SAFARI_PROFILE
        return _orig_init(self, *args, **kwargs)

    _patched_init._frakbox_impersonation_shim = True
    Session.__init__ = _patched_init
    Session._frakbox_impersonation_shim = True
    return True


_SHIM_APPLIED = _install_curl_cffi_impersonation_shim()
