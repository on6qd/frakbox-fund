"""tools package init.

Applies a process-wide monkeypatch that makes yfinance work behind the
egress proxy used in the remote research environment.

ROOT CAUSE (recurring 137x data_access friction):
    yfinance 1.x fetches every URL via curl_cffi with
    ``requests.Session(impersonate="chrome")`` (hardcoded in base.py,
    multi.py, scrapers/history.py, data.py). The Chrome TLS fingerprint
    fails the egress proxy's TLS handshake:
        curl: (35) TLS connect error: ...OPENSSL_internal:invalid library (0)
    The Safari fingerprint negotiates cleanly (verified: chrome -> SSLError,
    safari -> HTTP 200). So every price fetch dies until we swap the profile.

FIX:
    Wrap ``curl_cffi.requests.Session.__init__`` so any session created with
    ``impersonate="chrome"`` is transparently created with ``impersonate="safari"``
    instead. This covers ALL yfinance code paths without editing the installed
    package (which is not committable). Idempotent and safe: only the exact
    "chrome" value is rewritten; explicitly versioned profiles (e.g. "chrome110",
    "safari17_0") and non-yfinance curl_cffi usage are left untouched.

This runs on first ``import tools`` or ``import tools.<anything>`` — i.e. every
scanner / util that imports from this package gets the fix for free. Scripts
that import yfinance directly should ``import tools`` (or any tools submodule)
first; the canonical price helpers live in tools.yfinance_utils, which triggers
this automatically.
"""

_PROXY_SAFE_IMPERSONATE = "safari"


def _apply_curl_cffi_proxy_patch() -> bool:
    """Swap chrome->safari curl_cffi impersonation. Idempotent; returns True if applied."""
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        return False

    Session = getattr(_ccr, "Session", None)
    if Session is None:
        return False

    # Idempotency guard — don't double-wrap on re-import.
    if getattr(Session.__init__, "_frakbox_proxy_patched", False):
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = _PROXY_SAFE_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    _patched_init._frakbox_proxy_patched = True
    Session.__init__ = _patched_init
    return True


# Apply on import.
_curl_cffi_proxy_patch_applied = _apply_curl_cffi_proxy_patch()
