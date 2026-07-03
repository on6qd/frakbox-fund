"""tools package init — recurring cloud-infra fix (curl_cffi chrome->safari shim).

yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
(see yfinance/data.py). The Chrome TLS fingerprint fails the egress proxy's
handshake (curl error 35 / OPENSSL_internal invalid library), while the Safari
fingerprint negotiates cleanly (verified: chrome->SSLError, safari->HTTP 200).

This module monkeypatches ``curl_cffi.requests.Session.__init__`` to rewrite the
exact ``impersonate == "chrome"`` value to ``"safari"`` at construction time.
It is idempotent and ONLY rewrites the bare ``"chrome"`` value — versioned
profiles like ``"chrome124"`` are left untouched.

Importing anything from the ``tools`` package triggers this patch.
``tools/yfinance_utils.py`` imports the package to self-trigger; scanners that
import yfinance directly (e.g. nt_filing_scanner.py) also pull this in via the
package import.

NOTE (root cause of recurrence): this fix must be MERGED TO main to be
permanent. Sessions cut fresh branches from main, so if main lacks this file
every fresh clone loses it and every yfinance fetch breaks. Guarded by
tests/test_curl_cffi_shim.py.
"""

try:
    import curl_cffi.requests as _ccr

    if not getattr(_ccr.Session, "_frakbox_safari_shim", False):
        _orig_session_init = _ccr.Session.__init__

        def _patched_session_init(self, *args, **kwargs):
            if kwargs.get("impersonate") == "chrome":
                kwargs["impersonate"] = "safari"
            return _orig_session_init(self, *args, **kwargs)

        _ccr.Session.__init__ = _patched_session_init
        _ccr.Session._frakbox_safari_shim = True
except Exception:
    # Never let the shim break package imports; curl_cffi/yfinance may be absent
    # in some contexts (e.g. pure-DB tooling).
    pass
