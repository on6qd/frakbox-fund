"""tools package init.

RECURRING FRICTION #1 FIX (curl_cffi chrome->safari impersonation shim).

yfinance 1.2.0 hardcodes a curl_cffi Session with impersonate="chrome". In the
cloud egress-proxy environment, the Chrome TLS/JA3 fingerprint is rejected by
the proxy and every fetch fails with `curl: (35) Recv failure: Connection reset
by peer` (SSLError). The Safari fingerprint is accepted (HTTP 200).

This shim monkeypatches curl_cffi's Session so that any request asking for a
chrome* impersonation is transparently rewritten to safari. It is idempotent
and import-safe: importing anything from the `tools` package (the canonical
data path — yfinance_utils, timeseries, etc.) triggers this package __init__
and installs the patch before the first network call.

If yfinance/curl_cffi ever change so the Chrome fingerprint is accepted again,
this shim is harmless (Safari also returns 200).

Guarded by tests/test_curl_cffi_shim.py.
"""

_SHIM_FLAG = "_frakbox_curl_cffi_safari_shim_installed"


def _install_curl_cffi_safari_shim():
    """Rewrite chrome* impersonation -> safari on the curl_cffi Session class.

    Idempotent: safe to call many times; only wraps the originals once.
    """
    try:
        from curl_cffi import requests as _ccr
    except Exception:
        return  # curl_cffi not present; nothing to patch

    # Preferred safari fingerprint, with fallbacks across curl_cffi versions.
    _SAFARI = "safari15_5"

    def _rewrite(impersonate):
        if isinstance(impersonate, str) and impersonate.lower().startswith("chrome"):
            return _SAFARI
        return impersonate

    Session = getattr(_ccr, "Session", None)
    if Session is None or getattr(Session, _SHIM_FLAG, False):
        return

    _orig_init = Session.__init__
    _orig_request = Session.request

    def _patched_init(self, *args, **kwargs):
        if "impersonate" in kwargs:
            kwargs["impersonate"] = _rewrite(kwargs["impersonate"])
        else:
            kwargs["impersonate"] = _SAFARI
        return _orig_init(self, *args, **kwargs)

    def _patched_request(self, *args, **kwargs):
        if "impersonate" in kwargs:
            kwargs["impersonate"] = _rewrite(kwargs["impersonate"])
        return _orig_request(self, *args, **kwargs)

    Session.__init__ = _patched_init
    Session.request = _patched_request
    setattr(Session, _SHIM_FLAG, True)


_install_curl_cffi_safari_shim()
