"""tools package.

curl_cffi chrome->safari yfinance shim.

In this managed/proxied execution environment, curl_cffi's default "chrome"
TLS impersonation gets its connection reset by the outbound proxy
(curl error 35: "Recv failure: Connection reset by peer"), which breaks ALL
yfinance data access. Safari and edge impersonation profiles negotiate fine.

yfinance (and any code using curl_cffi.requests.Session) defaults to a
chrome* impersonation profile. This shim patches curl_cffi so that any
Session created with a chrome* profile (or no explicit profile) is
transparently remapped to "safari". Importing anything from the `tools`
package runs this shim first, so `from tools.yfinance_utils import ...`
(the mandated import path for all price fetching) is always protected.

This file has been lost repeatedly on fresh clones (see recurring
~139x data_access friction in the research journal). It MUST be committed
and merged to main to be a durable fix.
"""
from __future__ import annotations

_SAFE_IMPERSONATE = "safari"


def _install_curl_cffi_safari_shim() -> None:
    try:
        from curl_cffi import requests as _cr
    except Exception:
        return

    _OrigSession = _cr.Session

    if getattr(_OrigSession, "_frakbox_safari_shim", False):
        return

    def _remap(impersonate):
        # Remap chrome* (or unset) to a proxy-friendly safari profile.
        if impersonate is None:
            return _SAFE_IMPERSONATE
        if isinstance(impersonate, str) and impersonate.lower().startswith("chrome"):
            return _SAFE_IMPERSONATE
        return impersonate

    _orig_init = _OrigSession.__init__

    def _patched_init(self, *args, **kwargs):
        if "impersonate" in kwargs:
            kwargs["impersonate"] = _remap(kwargs.get("impersonate"))
        else:
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    _OrigSession.__init__ = _patched_init
    _OrigSession._frakbox_safari_shim = True

    # Some callers use the module-level request helpers (curl_cffi.requests.get
    # / .post) which build a throwaway Session under the hood; patch those too.
    for _fn in ("get", "post", "request", "head", "put", "delete", "patch", "options"):
        _orig = getattr(_cr, _fn, None)
        if _orig is None or getattr(_orig, "_frakbox_safari_shim", False):
            continue

        def _wrap(orig):
            def _inner(*args, **kwargs):
                if "impersonate" in kwargs:
                    kwargs["impersonate"] = _remap(kwargs.get("impersonate"))
                else:
                    kwargs["impersonate"] = _SAFE_IMPERSONATE
                return orig(*args, **kwargs)
            _inner._frakbox_safari_shim = True
            return _inner

        setattr(_cr, _fn, _wrap(_orig))


_install_curl_cffi_safari_shim()
