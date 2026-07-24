"""tools package init.

Applies a curl_cffi impersonation shim required for data access in the
cloud execution environment.

ROOT-CAUSE (recurring ~139x data_access friction): yfinance 1.2.0 hardcodes
``curl_cffi.requests.Session(impersonate="chrome")`` across its internals
(base.py / multi.py / scrapers/history.py / data.py). The Chrome TLS
fingerprint fails the egress proxy handshake ("curl: (35) Recv failure /
OPENSSL_internal invalid library"), while the Safari fingerprint negotiates
cleanly. We monkeypatch ``curl_cffi.requests.Session.__init__`` to rewrite the
*exact* ``impersonate="chrome"`` value to ``"safari"``.

The patch is:
  - idempotent (guarded by a module flag; safe to import many times)
  - narrow (only the literal string "chrome"; versioned profiles such as
    "chrome124" are left untouched)
  - self-triggering: any ``import tools`` / ``from tools import ...`` runs it,
    and tools/yfinance_utils.py imports the package so the canonical fetch path
    is always covered.

Verified: chrome -> SSLError(curl 35); safari -> HTTP 200 and SPY/AAPL rows
return. Guarded by tests/test_curl_cffi_shim.py.
"""

_CURL_CFFI_SHIM_APPLIED = False


def _apply_curl_cffi_safari_shim():
    """Rewrite curl_cffi Session impersonate='chrome' -> 'safari'.

    Returns True if the shim was applied (or already applied), False if
    curl_cffi is unavailable. Never raises: data access must degrade to the
    library default rather than crash package import.
    """
    global _CURL_CFFI_SHIM_APPLIED
    if _CURL_CFFI_SHIM_APPLIED:
        return True
    try:
        from curl_cffi import requests as _curl_requests
    except Exception:
        return False

    _Session = _curl_requests.Session
    _orig_init = _Session.__init__

    if getattr(_orig_init, "_safari_shim", False):
        _CURL_CFFI_SHIM_APPLIED = True
        return True

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim = True
    _Session.__init__ = _patched_init
    _CURL_CFFI_SHIM_APPLIED = True
    return True


_apply_curl_cffi_safari_shim()
