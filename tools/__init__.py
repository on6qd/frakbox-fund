"""Package init for tools/.

curl_cffi / yfinance proxy shim
-------------------------------
yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
in four places (base.py, multi.py, scrapers/history.py, data.py). Behind the
Claude Code agent proxy (a TLS-re-terminating MITM), curl_cffi's *chrome*
impersonation fails the handshake with::

    curl: (35) TLS connect error: error:00000000:invalid library (0):OPENSSL_internal

The *safari* impersonation profile negotiates cleanly through the same proxy
(verified HTTP 200). This shim transparently rewrites every curl_cffi Session
to use safari instead of chrome and points TLS verification at the proxy CA
bundle when one is configured. Importing anything from ``tools`` (which every
price-fetch path does) installs the patch process-wide and idempotently.

Root-caused 2026-06-26; re-applied on fresh clones where this file is absent.
Do NOT disable TLS verification — only the impersonation profile is changed.
"""

import os


def _install_curl_cffi_safari_shim():
    try:
        from curl_cffi import requests as _creq
    except Exception:
        return  # curl_cffi not installed; nothing to patch

    if getattr(_creq.Session, "_frakbox_safari_shim", False):
        return  # already patched

    # Locate a CA bundle to trust the proxy's re-terminated TLS.
    ca_bundle = (
        os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
        or os.environ.get("CURL_CA_BUNDLE")
    )
    if not ca_bundle or not os.path.exists(ca_bundle):
        for _candidate in ("/root/.ccr/ca-bundle.crt",):
            if os.path.exists(_candidate):
                ca_bundle = _candidate
                break

    _orig_init = _creq.Session.__init__

    def _patched_init(self, *args, **kwargs):
        # chrome -> safari: chrome's TLS fingerprint breaks through the MITM proxy.
        if kwargs.get("impersonate") == "chrome" or "impersonate" not in kwargs:
            kwargs["impersonate"] = "safari"
        if ca_bundle and "verify" not in kwargs:
            kwargs["verify"] = ca_bundle
        return _orig_init(self, *args, **kwargs)

    _creq.Session.__init__ = _patched_init
    _creq.Session._frakbox_safari_shim = True


_install_curl_cffi_safari_shim()
