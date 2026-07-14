"""Package init for tools.

RECURRING-FRICTION FIX (data_access #1, 137x): yfinance hardcodes a curl_cffi
Session with impersonate="chrome". The Chrome TLS fingerprint is rejected by the
egress proxy in the cloud execution environment (curl error 35: Recv failure /
OPENSSL_internal invalid, connection reset), while Safari negotiates cleanly
(verified: chrome -> curl35, safari -> HTTP200 against query1.finance.yahoo.com).

This monkeypatch wraps curl_cffi.requests.Session.__init__ so that any caller
requesting impersonate=="chrome" transparently gets "safari" instead. It is:
  - idempotent (guarded by a module flag; re-import is a no-op)
  - exact-match only (only the bare value "chrome" is rewritten; versioned
    profiles like "chrome124" are left untouched)
  - self-triggering: importing anything from the `tools` package runs this, and
    tools/yfinance_utils.py imports the package for exactly that reason.

Guarded by tests/test_curl_cffi_shim.py. Do NOT delete without a replacement —
every fresh clone that loses this file breaks all yfinance data access.
"""

_SHIM_FLAG = "_frakbox_chrome_to_safari_shim_applied"


def _apply_curl_cffi_chrome_to_safari_shim():
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        return

    Session = _ccr.Session
    if getattr(Session, _SHIM_FLAG, False):
        return

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    Session.__init__ = _patched_init
    setattr(Session, _SHIM_FLAG, True)

    # AsyncSession, if present, shares the same failure mode.
    _Async = getattr(_ccr, "AsyncSession", None)
    if _Async is not None and not getattr(_Async, _SHIM_FLAG, False):
        _orig_async_init = _Async.__init__

        def _patched_async_init(self, *args, **kwargs):
            if kwargs.get("impersonate") == "chrome":
                kwargs["impersonate"] = "safari"
            return _orig_async_init(self, *args, **kwargs)

        _Async.__init__ = _patched_async_init
        setattr(_Async, _SHIM_FLAG, True)


_apply_curl_cffi_chrome_to_safari_shim()
