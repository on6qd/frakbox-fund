"""tools package initializer.

CRITICAL OPERATIONAL SHIM (do not remove) — recurring #1 data_access friction.

yfinance (>=0.2.x, confirmed 1.2.0) hardcodes a curl_cffi session with
``impersonate="chrome"``. The Chrome TLS/JA3 fingerprint is rejected by this
environment's egress proxy (curl error 35: OPENSSL_internal / "Recv failure:
Connection reset by peer"), so every price fetch dies. Safari's fingerprint
negotiates cleanly (verified: chrome -> reset, safari -> HTTP 200).

Fix: monkeypatch curl_cffi's Session / AsyncSession so that the exact value
``impersonate="chrome"`` is rewritten to a Safari profile. Only the bare
``"chrome"`` string is touched — versioned profiles (e.g. ``chrome124``) are
left alone. Idempotent and import-safe: if curl_cffi is absent or its API
shape changes, we degrade silently rather than break imports.

The target profile defaults to ``safari15_5`` and can be overridden with the
``CURL_CFFI_IMPERSONATE`` environment variable.

Importing any submodule of ``tools`` runs this first, so yfinance_utils.py
(and any module that does ``from tools... import``) self-triggers the patch.
Modules that ``import yfinance`` directly should ``import tools  # noqa`` at
the top to arm the shim.
"""
from __future__ import annotations

import os

_TARGET = os.environ.get("CURL_CFFI_IMPERSONATE", "safari15_5")


def _rewrite(value):
    return _TARGET if value == "chrome" else value


def _install_curl_cffi_shim() -> bool:
    """Patch curl_cffi Session/AsyncSession to swap chrome -> safari.

    Returns True if the patch was applied (or already applied), False if
    curl_cffi is unavailable or the API shape was unexpected.
    """
    try:
        import curl_cffi.requests as ccr
    except Exception:
        return False

    applied = False
    for cls_name in ("Session", "AsyncSession"):
        cls = getattr(ccr, cls_name, None)
        if cls is None:
            continue

        # --- __init__ patch ---
        orig_init = cls.__init__
        if not getattr(orig_init, "_chrome_safari_shim", False):
            def make_init(orig):
                def patched_init(self, *args, **kwargs):
                    if kwargs.get("impersonate") == "chrome":
                        kwargs["impersonate"] = _TARGET
                    return orig(self, *args, **kwargs)
                patched_init._chrome_safari_shim = True
                return patched_init
            cls.__init__ = make_init(orig_init)
            applied = True

        # --- request patch (yfinance may pass impersonate per-request) ---
        orig_request = getattr(cls, "request", None)
        if orig_request is not None and not getattr(orig_request, "_chrome_safari_shim", False):
            def make_request(orig):
                def patched_request(self, *args, **kwargs):
                    if kwargs.get("impersonate") == "chrome":
                        kwargs["impersonate"] = _TARGET
                    return orig(self, *args, **kwargs)
                patched_request._chrome_safari_shim = True
                return patched_request
            cls.request = make_request(orig_request)
            applied = True

    return applied


# Arm on import. Never let a shim failure break the package import.
try:
    _install_curl_cffi_shim()
except Exception:
    pass
