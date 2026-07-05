"""tools package.

Importing this package installs a curl_cffi TLS-fingerprint shim that is
REQUIRED for any yfinance fetch to succeed behind the egress proxy.

Root cause: yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
in base.py / multi.py / scrapers/history.py / data.py. The Chrome TLS fingerprint
fails the egress-proxy handshake (curl error 35, OPENSSL_internal invalid library),
while the Safari fingerprint negotiates cleanly (verified: chrome -> SSLError,
safari -> HTTP 200).

Fix: monkeypatch ``curl_cffi.requests.Session.__init__`` to rewrite an
``impersonate`` value of exactly ``"chrome"`` to ``"safari"``. The patch is:
  - idempotent (safe to import repeatedly; applied at most once),
  - narrow (only the bare ``"chrome"`` value is rewritten; versioned Chrome
    profiles such as ``"chrome124"`` are left untouched),
  - transparent (all other kwargs pass through unchanged).

Any module that fetches prices should import the ``tools`` package (directly or
via ``tools.yfinance_utils``) so the shim is installed before the first request.
Modules that import ``yfinance`` directly (e.g. nt_filing_scanner.py) must import
``tools`` first for the same reason.
"""

_CURL_CFFI_SHIM_APPLIED = False


def _install_curl_cffi_safari_shim():
    """Rewrite curl_cffi chrome impersonation to safari. Idempotent."""
    global _CURL_CFFI_SHIM_APPLIED
    if _CURL_CFFI_SHIM_APPLIED:
        return True
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        # curl_cffi not installed / importable — nothing to patch.
        return False

    Session = _ccr.Session
    if getattr(Session.__init__, "_chrome_to_safari_shim", False):
        _CURL_CFFI_SHIM_APPLIED = True
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._chrome_to_safari_shim = True
    Session.__init__ = _patched_init
    _CURL_CFFI_SHIM_APPLIED = True
    return True


# Install on package import so any downstream yfinance use is covered.
_install_curl_cffi_safari_shim()
