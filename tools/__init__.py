"""tools package init — curl_cffi impersonation shim for yfinance.

Recurring infrastructure fix (friction category #1, ~140+ recurrences).

The agent/execution proxy resets connections that use curl_cffi's default
"chrome" TLS impersonation (yields `curl: (35) Recv failure: Connection reset
by peer`, surfaced by yfinance as an SSLError and "no data" errors). Safari
impersonation negotiates cleanly through the same proxy and returns HTTP 200.

yfinance 1.2.0 hardcodes `curl_cffi.requests.Session(impersonate="chrome")` in
four call sites (base.py, multi.py, scrapers/history.py, data.py). Rather than
patch each site, we replace the `Session` constructor on the shared
`curl_cffi.requests` module so any "chrome" (or default) impersonation is
rewritten to "safari" at construction time.

Importing anything from the `tools` package activates the shim. tools modules
that hit the network (yfinance_utils, timeseries, scanners) all live under this
package, so the shim is guaranteed to run before any download.

NOTE: this file must be merged to `main`. It is re-lost on every fresh clone
because it has historically only lived on ephemeral work branches.
"""
from __future__ import annotations

# curl_cffi impersonation profile that survives the proxy (chrome is reset).
_IMPERSONATE_TARGET = "safari"


def _install_curl_cffi_impersonation_shim() -> bool:
    """Rewrite chrome/default curl_cffi impersonation to safari. Idempotent."""
    try:
        from curl_cffi import requests as _cc_requests
    except Exception:
        return False

    _OrigSession = _cc_requests.Session
    if getattr(_OrigSession, "_frakbox_impersonation_shim", False):
        return True  # already installed

    class _ShimSession(_OrigSession):
        _frakbox_impersonation_shim = True

        def __init__(self, *args, **kwargs):
            imp = kwargs.get("impersonate")
            if imp is None or str(imp).startswith("chrome"):
                kwargs["impersonate"] = _IMPERSONATE_TARGET
            super().__init__(*args, **kwargs)

    _cc_requests.Session = _ShimSession
    # Keep the submodule attribute (curl_cffi.requests.session.Session) coherent
    # so isinstance(session, requests.session.Session) checks still pass.
    try:
        _cc_requests.session.Session = _ShimSession
    except Exception:
        pass
    return True


_shim_installed = _install_curl_cffi_impersonation_shim()
