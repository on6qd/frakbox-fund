"""
tools package init — curl_cffi chrome->safari impersonation shim.

WHY THIS FILE EXISTS (do not delete — this is the single highest-leverage
infra fix in the repo; fresh clones have silently lost it ~17 times):

yfinance 1.2.0 hardcodes `curl_cffi.requests.Session(impersonate="chrome")`
in its internal HTTP paths. The Chrome TLS fingerprint fails the egress
proxy handshake (curl error 35, OPENSSL_internal invalid library), so every
price fetch returns "no data". The Safari fingerprint negotiates cleanly
(verified: chrome -> SSLError, safari -> HTTP 200).

FIX: monkeypatch curl_cffi's Session.__init__ to rewrite the *exact* value
impersonate=="chrome" -> "safari". Idempotent, and versioned Chrome profiles
(e.g. "chrome124") are left untouched — only the bare "chrome" alias yfinance
uses is remapped.

This lives in tools/__init__.py so that ANY `from tools.<x> import ...` (the
canonical path — yfinance_utils, the scanners, data_tasks helpers all import
from the tools package) triggers the patch before yfinance builds a session.
Direct `import yfinance` callers (e.g. nt_filing_scanner) also import from the
tools package, so they are covered too.

See knowledge: yfinance_curl_cffi_chrome_to_safari_proxy_patch_2026_06_26,
tools_init_shim_recurring_loss_root_cause_2026_07_02.
"""

_SHIM_APPLIED = False


def _apply_curl_cffi_safari_shim():
    """Rewrite curl_cffi impersonate=='chrome' -> 'safari'. Idempotent."""
    global _SHIM_APPLIED
    if _SHIM_APPLIED:
        return
    try:
        import curl_cffi.requests as _cr
    except Exception:
        # curl_cffi not installed / not importable — nothing to patch.
        return

    _orig_init = _cr.Session.__init__

    # Guard against double-patching within a single process (e.g. reimport).
    if getattr(_orig_init, "_chrome_to_safari_shim", False):
        _SHIM_APPLIED = True
        return

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._chrome_to_safari_shim = True
    _cr.Session.__init__ = _patched_init
    _SHIM_APPLIED = True


_apply_curl_cffi_safari_shim()
