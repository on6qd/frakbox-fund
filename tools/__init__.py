"""tools package init.

curl_cffi impersonation shim (REQUIRED in this environment)
-----------------------------------------------------------
yfinance builds its HTTP client with ``curl_cffi.requests.Session(impersonate="chrome")``.
In the sandboxed remote-execution environment all outbound HTTPS is tunnelled through a
TLS-re-terminating egress proxy. The *newest* Chrome TLS fingerprint that curl_cffi's
``impersonate="chrome"`` alias resolves to is rejected by that proxy — every request dies
with ``curl: (35) Recv failure: Connection reset by peer``. Older pinned fingerprints
(``chrome110``, ``safari``) pass cleanly, and curl_cffi already picks up the proxy and CA
bundle from the standard environment variables, so the ONLY thing that needs fixing is the
impersonation target.

This shim monkeypatches ``curl_cffi.requests.Session.__init__`` so that any construction
requesting the blocked ``impersonate="chrome"`` alias is transparently rewritten to a
pinned, proxy-compatible fingerprint. Explicit non-"chrome" values are left untouched.

Because ``import tools.yfinance_utils`` (and every other tools submodule) triggers this
``__init__`` first, the patch is always installed before yfinance makes a request.

This file keeps getting lost on fresh session clones (session branches are never merged
back to default). If price fetches fail with connection-reset again, restore this shim and
run ``python3 -m pytest tests/test_curl_cffi_shim.py``.
"""

from __future__ import annotations

# Fingerprint we rewrite the blocked "chrome" alias to. chrome110 is the closest
# still-passing Chrome fingerprint, so downstream behaviour stays Chrome-like.
_SAFE_IMPERSONATE = "chrome110"
_BLOCKED_ALIASES = {"chrome"}


def _install_curl_cffi_impersonate_shim() -> None:
    try:
        from curl_cffi import requests as _cr
    except Exception:
        # curl_cffi not installed / not used — nothing to patch.
        return

    Session = getattr(_cr, "Session", None)
    if Session is None or getattr(Session, "_ccr_impersonate_shim", False):
        return

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):  # noqa: ANN001
        imp = kwargs.get("impersonate")
        if imp in _BLOCKED_ALIASES:
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    Session.__init__ = _patched_init
    Session._ccr_impersonate_shim = True


_install_curl_cffi_impersonate_shim()
