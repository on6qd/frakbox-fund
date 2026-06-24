"""tools package.

Importing anything from ``tools`` installs the egress-proxy TLS compatibility
patch for yfinance/curl_cffi (see ``_install_proxy_tls_patch`` below). This is
the single most reliable hook because every scanner and data task in the
project reaches yfinance via a ``from tools.X import ...`` statement, which
runs this module first.
"""

import os


def _install_proxy_tls_patch() -> None:
    """Make yfinance's curl_cffi requests work through the cloud egress proxy.

    yfinance 1.x fetches data with curl_cffi using ``impersonate="chrome"``,
    whose TLS fingerprint fails the policy proxy's TLS re-termination with a
    BoringSSL "TLS connect error: invalid library" (curl error 35). That blocks
    ALL price fetching. We patch curl_cffi's Session so that, only when an
    HTTPS proxy is configured, we (1) swap the failing chrome fingerprint for
    safari (which negotiates cleanly through the proxy) and (2) force the CA
    bundle that curl_cffi otherwise ignores while impersonating.

    Idempotent and a no-op when no proxy is present.
    """
    proxied = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    if not proxied:
        return
    try:
        import curl_cffi.requests as _cr
    except Exception:
        return
    if getattr(_cr.Session, "_frakbox_proxy_patched", False):
        return
    ca = (os.environ.get("CURL_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE")
          or os.environ.get("REQUESTS_CA_BUNDLE"))
    _orig_init = _cr.Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") in (None, "chrome"):
            kwargs["impersonate"] = "safari"
        if ca:
            kwargs.setdefault("verify", ca)
        return _orig_init(self, *args, **kwargs)

    _cr.Session.__init__ = _patched_init
    _cr.Session._frakbox_proxy_patched = True


_install_proxy_tls_patch()
