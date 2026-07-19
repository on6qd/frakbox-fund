"""tools package init — curl_cffi TLS-impersonation shim for the egress proxy.

RECURRING FRICTION FIX (data_access, 137x+). yfinance (>=1.2) drives its HTTP
through curl_cffi with a hardcoded `impersonate="chrome"` TLS fingerprint. The
managed egress proxy REJECTS the Chrome ClientHello (curl error 35: "Recv
failure / OPENSSL_internal invalid library") while it ACCEPTS Safari's. Verified
empirically: chrome -> SSLError, safari -> HTTP 200, live SPY/AAPL fetch returns
rows.

Fix: monkeypatch curl_cffi.requests.Session.__init__ so that the exact value
`impersonate="chrome"` is rewritten to `"safari"`. The patch is:
  - idempotent (guarded by a sentinel so re-imports don't double-wrap),
  - narrow (only the literal "chrome"; versioned profiles like "chrome124" and
    any other impersonation string are left untouched),
  - self-triggering (importing anything from `tools` runs this init; e.g.
    tools.yfinance_utils and tools.nt_filing_scanner import the package).

IMPORTANT: this file must be COMMITTED. Prior sessions repeatedly "fixed" this
without committing, so every fresh clone lost it. Guarded by
tests/test_curl_cffi_shim.py.
"""

from __future__ import annotations


def _install_curl_cffi_safari_shim() -> None:
    try:
        from curl_cffi import requests as _ccr
    except Exception:
        return

    Session = getattr(_ccr, "Session", None)
    if Session is None:
        return

    # Idempotency guard: don't wrap twice across repeated imports.
    if getattr(Session.__init__, "_chrome_to_safari_shim", False):
        return

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return _orig_init(self, *args, **kwargs)

    _patched_init._chrome_to_safari_shim = True  # type: ignore[attr-defined]
    Session.__init__ = _patched_init  # type: ignore[method-assign]

    # AsyncSession shares the same failure mode when present.
    ASession = getattr(_ccr, "AsyncSession", None)
    if ASession is not None and not getattr(
        ASession.__init__, "_chrome_to_safari_shim", False
    ):
        _orig_ainit = ASession.__init__

        def _patched_ainit(self, *args, **kwargs):
            if kwargs.get("impersonate") == "chrome":
                kwargs["impersonate"] = "safari"
            return _orig_ainit(self, *args, **kwargs)

        _patched_ainit._chrome_to_safari_shim = True  # type: ignore[attr-defined]
        ASession.__init__ = _patched_ainit  # type: ignore[method-assign]


_install_curl_cffi_safari_shim()
