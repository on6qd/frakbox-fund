"""tools package init — applies the egress-proxy shim for yfinance/curl_cffi.

Recurring friction #1 (logged 137x). Root cause
------------------------------------------------
yfinance 1.2.0 instantiates its HTTP client as
``curl_cffi.requests.Session(impersonate="chrome")`` (see yfinance/base.py,
data.py, multi.py). The Chrome TLS fingerprint is rejected by this
environment's egress HTTPS proxy with::

    curl: (35) TLS connect error: OPENSSL_internal:invalid library (0)

so every price fetch fails (``yfinance returned no data``). The Safari and Edge
fingerprints pass the proxy cleanly (verified: chrome -> curl 35, safari ->
HTTP 200). yfinance exposes no setting to change the impersonation, so we
monkeypatch curl_cffi's ``Session`` to transparently rewrite any ``chrome*``
impersonation to ``safari``.

Why here
--------
This module runs on first import of anything under ``tools.*`` — which is the
canonical price path (``tools.yfinance_utils.safe_download`` is mandated by
CLAUDE.md and imported by 100+ modules; ``tools.timeseries`` and
``market_data`` both route through it). Importing any ``tools`` submodule
therefore applies the shim before yfinance lazily creates its session.

Prior sessions repeatedly "fixed" this without committing the file to the
branch base, so it kept reappearing in fresh clones. It is now committed.
"""

from __future__ import annotations


def _apply_curl_cffi_impersonate_shim() -> None:
    """Rewrite curl_cffi Session(impersonate='chrome*') -> 'safari'.

    Idempotent and best-effort: if curl_cffi is absent or its internals change,
    we silently skip rather than break imports.
    """
    try:
        from curl_cffi import requests as _cr
    except Exception:
        return

    session_cls = getattr(_cr, "Session", None)
    if session_cls is None or getattr(session_cls, "_frakbox_impersonate_patched", False):
        return

    _orig_init = session_cls.__init__

    def _patched_init(self, *args, **kwargs):
        imp = kwargs.get("impersonate")
        if isinstance(imp, str) and imp.lower().startswith("chrome"):
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    session_cls.__init__ = _patched_init
    session_cls._frakbox_impersonate_patched = True


_apply_curl_cffi_impersonate_shim()
