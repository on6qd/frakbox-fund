"""
tools package init — curl_cffi Chrome→Safari impersonation shim.

WHY THIS FILE EXISTS (recurring #1 data_access friction, ~142x):
yfinance 1.2.0 hardcodes `curl_cffi.requests.Session(impersonate="chrome")`
across base.py / multi.py / scrapers / data.py. The Chrome TLS (JA3)
fingerprint fails the egress proxy handshake (curl error 35, "Connection
reset by peer" / OPENSSL_internal invalid library), while the Safari
fingerprint negotiates cleanly. Verified: chrome -> SSLError, safari -> HTTP200.

FIX: monkeypatch curl_cffi.requests.Session.__init__ (and .request) so that
an `impersonate == "chrome"` argument is rewritten to a Safari profile.
Idempotent, only rewrites the exact bare value "chrome" (versioned Chrome
profiles like "chrome110" are left untouched). Importing the `tools` package
self-triggers the patch; yfinance_utils.py imports the package, and scanners
that import yfinance directly should `import tools` first.

The Safari profile can be overridden with env var CURL_CFFI_IMPERSONATE
(default "safari15_5").

IMPORTANT: This shim keeps getting lost on fresh clones because it has never
been merged to main. Until it lands on main, EVERY fresh clone re-loses
yfinance data access. See knowledge: yfinance_curl_cffi_shim_*.
"""
import os

_TARGET = os.environ.get("CURL_CFFI_IMPERSONATE", "safari15_5")
_applied = False


def _apply_curl_cffi_shim():
    """Rewrite curl_cffi Session impersonate=='chrome' -> Safari. Idempotent."""
    global _applied
    if _applied:
        return
    try:
        import curl_cffi.requests as _cc
    except Exception:
        # curl_cffi not installed / import failure — nothing to patch.
        _applied = True
        return

    def _rewrite(kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = _TARGET
        return kwargs

    Session = getattr(_cc, "Session", None)
    if Session is None:
        _applied = True
        return

    if not getattr(Session.__init__, "_chrome_safari_shim", False):
        _orig_init = Session.__init__

        def _patched_init(self, *args, **kwargs):
            return _orig_init(self, *args, **_rewrite(kwargs))

        _patched_init._chrome_safari_shim = True
        Session.__init__ = _patched_init

    if hasattr(Session, "request") and not getattr(
        Session.request, "_chrome_safari_shim", False
    ):
        _orig_request = Session.request

        def _patched_request(self, *args, **kwargs):
            return _orig_request(self, *args, **_rewrite(kwargs))

        _patched_request._chrome_safari_shim = True
        Session.request = _patched_request

    # AsyncSession, if present, uses the same impersonate kwarg.
    Async = getattr(_cc, "AsyncSession", None)
    if Async is not None and not getattr(
        Async.__init__, "_chrome_safari_shim", False
    ):
        _orig_ainit = Async.__init__

        def _patched_ainit(self, *args, **kwargs):
            return _orig_ainit(self, *args, **_rewrite(kwargs))

        _patched_ainit._chrome_safari_shim = True
        Async.__init__ = _patched_ainit

    _applied = True


_apply_curl_cffi_shim()
