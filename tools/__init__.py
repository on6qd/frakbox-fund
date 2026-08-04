"""tools package init.

PERMANENT FIX for the recurring data_access friction (curl 35 TLS handshake
failure on fresh clones behind the egress proxy).

yfinance hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``. The
Chrome TLS fingerprint fails the egress-proxy handshake (curl 35 /
"Recv failure: Connection reset by peer" or OPENSSL_internal invalid library),
while the Safari fingerprint negotiates cleanly. This module monkeypatches
``curl_cffi.requests.Session.__init__`` so that an ``impersonate`` value of
exactly ``"chrome"`` is rewritten to ``"safari"`` before the real __init__
runs. It is idempotent and only touches the exact string ``"chrome"`` —
explicitly versioned profiles (e.g. ``chrome124``) are left untouched.

Importing the ``tools`` package (which happens whenever tools/yfinance_utils.py
or any other tool is imported) self-triggers the patch. Scanners that import
yfinance directly should ``import tools`` first (or import any tools.* module).
"""

_PATCHED = False


def _install_curl_cffi_safari_shim():
    global _PATCHED
    if _PATCHED:
        return
    try:
        from curl_cffi import requests as _ccr
    except Exception:
        return

    _Session = _ccr.Session
    _orig_init = _Session.__init__

    # Avoid double-wrapping (idempotent across repeated imports / reloads).
    if getattr(_orig_init, "_safari_shim", False):
        _PATCHED = True
        return

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim = True
    _Session.__init__ = _patched_init
    _PATCHED = True


_install_curl_cffi_safari_shim()
