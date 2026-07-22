"""
tools package init.

PERMANENT FIX for the recurring data_access friction: yfinance 1.2.0 hardcodes
curl_cffi requests.Session(impersonate="chrome") across base.py/multi.py/
scrapers/history.py/data.py. The Chrome TLS fingerprint fails the egress proxy
handshake (curl error 35: OPENSSL_internal invalid library / TLS reset), while
the Safari fingerprint negotiates cleanly (verified: chrome -> SSLError,
safari -> HTTP 200).

Fix: monkeypatch curl_cffi.requests.Session.__init__ to rewrite the exact
impersonate value "chrome" -> "safari". Idempotent, and only the bare "chrome"
value is rewritten (versioned Chrome profiles like "chrome124" are left
untouched). Importing anything from the `tools` package (e.g.
`from tools.yfinance_utils import get_close_prices`) applies the patch before
any yfinance download runs.
"""
from __future__ import annotations


def _patch_curl_cffi_impersonate() -> None:
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        return

    Session = getattr(_ccr, "Session", None)
    if Session is None:
        return

    if getattr(Session, "_frakbox_safari_shim", False):
        return  # already patched (idempotent)

    _orig_init = Session.__init__

    def _init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    Session.__init__ = _init
    Session._frakbox_safari_shim = True


_patch_curl_cffi_impersonate()
