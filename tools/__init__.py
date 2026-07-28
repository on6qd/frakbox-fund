"""tools package initializer.

RECURRING INFRA SHIM (see friction_log #1 + research_journal 2026-07-*):
yfinance >=0.2.x issues its HTTP requests through curl_cffi with
``impersonate="chrome"``. In this cloud/proxy environment the Chrome TLS
fingerprint is reset by the peer (curl error 35, "Recv failure: Connection
reset by peer"), so every yfinance download returns zero rows. The Safari
fingerprint negotiates cleanly (HTTP 200).

This module monkeypatches ``curl_cffi.requests.Session`` at import time so any
``impersonate="chrome"`` (the yfinance default, hard-coded in base.py/data.py/
multi.py) is transparently rewritten to ``impersonate="safari"``. Importing the
``tools`` package (e.g. ``from tools.yfinance_utils import get_close_prices``)
is enough to activate it — no call sites change.

Because ``tools/__init__.py`` is regenerated on every fresh clone (it has never
been merged to main), this file is intentionally self-contained and defensive:
it never raises if curl_cffi is absent or already patched.
"""

from __future__ import annotations

# Chrome fingerprints are reset by the proxy; Safari works. Rewrite the default.
_BROKEN_IMPERSONATIONS = {"chrome", None, ""}
_SAFE_IMPERSONATION = "safari"


def _install_curl_cffi_safari_shim() -> bool:
    """Patch curl_cffi.requests.Session to prefer Safari impersonation.

    Returns True if the patch was installed (or already present), False if
    curl_cffi is unavailable. Never raises.
    """
    try:
        from curl_cffi import requests as _cr
    except Exception:
        return False

    Session = getattr(_cr, "Session", None)
    if Session is None:
        return False

    # Idempotent: don't double-wrap on re-import.
    if getattr(Session.__init__, "_safari_shim_installed", False):
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        imp = kwargs.get("impersonate", "chrome")
        if imp in _BROKEN_IMPERSONATIONS or (isinstance(imp, str) and imp.startswith("chrome")):
            kwargs["impersonate"] = _SAFE_IMPERSONATION
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim_installed = True  # type: ignore[attr-defined]
    Session.__init__ = _patched_init  # type: ignore[method-assign]
    return True


SHIM_INSTALLED = _install_curl_cffi_safari_shim()
