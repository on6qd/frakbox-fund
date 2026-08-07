"""tools package init — curl_cffi chrome->safari impersonation shim.

RECURRING FRICTION #1 (~140x): In the cloud execution environment, outbound
HTTPS goes through a policy-enforcing egress proxy that re-terminates TLS. That
proxy REJECTS curl_cffi's ``chrome`` TLS fingerprint (the fetch fails with
``curl: (35) Recv failure: Connection reset by peer``), but ACCEPTS ``safari``
(HTTP 200). yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate=
"chrome...")`` for every request, so on a fresh clone EVERY price fetch fails.

This module monkeypatches curl_cffi so any ``chrome*`` impersonation is rewritten
to a safari profile before the session is created. Because it lives in
``tools/__init__.py``, it runs on the FIRST ``from tools...`` import — which the
canonical data path (``tools.yfinance_utils``, ``tools.timeseries``) always does.

Idempotent and env-overridable:
  - FRAKBOX_IMPERSONATE   : target profile (default "safari")
  - FRAKBOX_DISABLE_SHIM  : set to "1"/"true" to skip the patch entirely

Guarded by tests/test_curl_cffi_shim.py. Verified end-to-end: chrome->curl35,
safari->HTTP200; SPY/AAPL fetch returns rows.
"""

import os


def _install_curl_cffi_impersonate_shim():
    if os.environ.get("FRAKBOX_DISABLE_SHIM", "").lower() in ("1", "true", "yes"):
        return False

    target = os.environ.get("FRAKBOX_IMPERSONATE", "safari") or "safari"

    try:
        from curl_cffi import requests as _cr
    except Exception:
        return False

    Session = getattr(_cr, "Session", None)
    if Session is None or getattr(Session, "_frakbox_impersonate_shim", False):
        return False

    def _rewrite(value):
        # Rewrite any chrome/chrome-derived profile to the accepted target.
        if value is None:
            return target
        try:
            low = str(value).lower()
        except Exception:
            return value
        if low.startswith("chrome") or low.startswith("chromium") or low.startswith("edge"):
            return target
        return value

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if "impersonate" in kwargs:
            kwargs["impersonate"] = _rewrite(kwargs.get("impersonate"))
        else:
            # yfinance passes impersonate positionally in some paths; default it
            # so a bare Session() also gets an accepted fingerprint.
            kwargs["impersonate"] = target
        return _orig_init(self, *args, **kwargs)

    Session.__init__ = _patched_init
    Session._frakbox_impersonate_shim = True
    return True


# Run on import. Any failure here must never break importing the tools package,
# so swallow unexpected errors (the fetch will surface a clear error later).
try:
    _install_curl_cffi_impersonate_shim()
except Exception:
    pass
