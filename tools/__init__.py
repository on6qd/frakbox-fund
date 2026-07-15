"""Package init for `tools`.

Recurring infra fix (data_access friction #1, 137x): yfinance 1.2.0 hardcodes
`curl_cffi.requests.Session(impersonate="chrome")`. In the cloud execution
environment the egress proxy rejects the Chrome TLS/JA3 fingerprint with
`curl: (35) Recv failure: Connection reset by peer`, so every yfinance fetch
fails. The Safari fingerprint is accepted by the proxy.

This module monkeypatches `curl_cffi.requests.Session` so any request for
Chrome impersonation is transparently rewritten to Safari. It runs on the
first `from tools import ...` / `import tools.<x>` — which is the canonical
entry path for all data code in this repo (yfinance_utils, timeseries, etc.).

Because the fix has been lost on every fresh clone (it must be merged to main
to be permanent), it is reconstructed here from the knowledge-base description
and guarded by tests/test_curl_cffi_shim.py.
"""

_CHROME_TOKENS = ("chrome", "chrome99", "chrome100", "chrome101", "chrome104",
                  "chrome107", "chrome110", "chrome116", "chrome119", "chrome120",
                  "chrome123", "chrome124", "chrome131", "chrome99_android",
                  "chrome_android")
_SAFARI_TARGET = "safari"


def _rewrite_impersonate(value):
    """Return the impersonation string to actually use.

    Any Chrome fingerprint (which the proxy blocks) is rewritten to Safari.
    Everything else (including None or an explicit safari*) is left as-is.
    """
    if isinstance(value, str) and value.lower() in _CHROME_TOKENS:
        return _SAFARI_TARGET
    return value


def _apply_curl_cffi_shim():
    try:
        from curl_cffi import requests as _ccr
    except Exception:
        return False

    _OrigSession = _ccr.Session
    if getattr(_OrigSession, "_frakbox_chrome_to_safari", False):
        return True  # already patched

    _orig_init = _OrigSession.__init__

    def _patched_init(self, *args, **kwargs):
        if "impersonate" in kwargs:
            kwargs["impersonate"] = _rewrite_impersonate(kwargs["impersonate"])
        return _orig_init(self, *args, **kwargs)

    _OrigSession.__init__ = _patched_init
    _OrigSession._frakbox_chrome_to_safari = True

    # yfinance also constructs AsyncSession in some paths; patch if present.
    _AsyncSession = getattr(_ccr, "AsyncSession", None)
    if _AsyncSession is not None and not getattr(
        _AsyncSession, "_frakbox_chrome_to_safari", False
    ):
        _orig_async_init = _AsyncSession.__init__

        def _patched_async_init(self, *args, **kwargs):
            if "impersonate" in kwargs:
                kwargs["impersonate"] = _rewrite_impersonate(kwargs["impersonate"])
            return _orig_async_init(self, *args, **kwargs)

        _AsyncSession.__init__ = _patched_async_init
        _AsyncSession._frakbox_chrome_to_safari = True

    return True


_SHIM_APPLIED = _apply_curl_cffi_shim()
