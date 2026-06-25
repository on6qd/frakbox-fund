"""tools package.

Importing any ``tools.X`` submodule triggers this package initializer, which
installs a one-time, idempotent fix for yfinance price fetches in the cloud
research session.

Why this exists
---------------
yfinance 1.x fetches Yahoo data through ``curl_cffi`` with a Chrome TLS
fingerprint (``requests.Session(impersonate="chrome")``, hard-coded in four
places inside yfinance: base.py, multi.py, scrapers/history.py, data.py).

In the cloud session, all outbound HTTPS is re-terminated by a policy egress
proxy (``HTTPS_PROXY`` is set). The Chrome BoringSSL fingerprint fails the
handshake against that proxy with::

    curl: (35) TLS connect error: error:00000000:invalid library (0):
    OPENSSL_internal:invalid library (0)

This happens during the TLS handshake, *before* certificate verification, so
pointing curl at the CA bundle does not help. Empirically the Safari
fingerprint negotiates cleanly through the same proxy (verified: chrome ->
SSLError, safari -> HTTP 200).

The fix is therefore a narrow monkeypatch: when a proxy is in effect, rewrite
``impersonate="chrome*"`` to ``impersonate="safari"`` for every curl_cffi
Session yfinance creates. It is a no-op when no proxy is set (local runs keep
Chrome) and is safe to import repeatedly.

This lives in ``tools/__init__.py`` because every research entry point
(data_tasks.py, oos_tracker.py, market_data.py, scanners, ...) imports at least
one ``tools.X`` submodule before any price fetch, so the patch is guaranteed to
run before yfinance opens a session.
"""

from __future__ import annotations

import os


def _patch_curl_cffi_for_proxy() -> None:
    """Swap Chrome -> Safari impersonation for curl_cffi behind an egress proxy.

    Idempotent and defensive: any failure is swallowed so a missing/changed
    curl_cffi never blocks importing the tools package.
    """
    # Only needed when outbound HTTPS is funneled through the policy proxy.
    if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        return

    try:
        from curl_cffi import requests as _ccr
    except Exception:
        return

    session_cls = getattr(_ccr, "Session", None)
    if session_cls is None:
        return

    # Already patched? (sentinel survives repeated imports within a process)
    if getattr(session_cls, "_frakbox_proxy_patched", False):
        return

    orig_init = session_cls.__init__

    def patched_init(self, *args, **kwargs):
        imp = kwargs.get("impersonate")
        if isinstance(imp, str) and imp.lower().startswith("chrome"):
            kwargs["impersonate"] = "safari"
        # Belt-and-suspenders: honor the proxy CA bundle if curl_cffi ignored
        # the standard env vars while impersonating.
        if "verify" not in kwargs:
            ca = (
                os.environ.get("CURL_CA_BUNDLE")
                or os.environ.get("REQUESTS_CA_BUNDLE")
                or os.environ.get("SSL_CERT_FILE")
            )
            if ca and os.path.exists(ca):
                kwargs["verify"] = ca
        return orig_init(self, *args, **kwargs)

    try:
        session_cls.__init__ = patched_init
        session_cls._frakbox_proxy_patched = True
    except Exception:
        # If the class is immutable for some reason, leave yfinance as-is.
        return


_patch_curl_cffi_for_proxy()
