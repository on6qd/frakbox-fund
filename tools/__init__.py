"""tools package init.

curl_cffi impersonation shim (recurring fresh-clone data-access fix).

Why this exists
---------------
yfinance (>=1.x) creates its HTTP sessions with ``curl_cffi.requests.Session(
impersonate="chrome")``. In this environment outbound HTTPS goes through an
agent proxy that RESETS the connection for the ``chrome`` TLS fingerprint
("curl: (35) Recv failure: Connection reset by peer" / SSLError). The ``safari``
fingerprint negotiates cleanly and returns HTTP 200. Without this shim EVERY
fresh clone silently loses all yfinance price data — the single most frequent
recurring friction in this project (~140 recorded occurrences).

What it does
------------
Monkeypatches ``curl_cffi.requests.Session`` so that any request asking to
impersonate ``chrome`` (or any chrome* variant, which is yfinance's hardcoded
default) is transparently rewritten to ``safari``. Everything else is left
untouched. The patch is applied at both the session-construction level and the
per-request level, and it is fully idempotent — importing ``tools`` more than
once is a no-op.

This is intentionally defensive: if curl_cffi is absent or its internals differ,
the shim degrades to a silent no-op rather than breaking imports.
"""

import os

# Allow opting out (e.g. running on a host with direct network access).
_DISABLE = os.environ.get("FRAKBOX_DISABLE_CURL_CFFI_SHIM", "").lower() in (
    "1",
    "true",
    "yes",
)

# The impersonation target that survives the agent proxy. Override-able so a
# future proxy change can be handled with an env var instead of a code edit.
_SAFE_IMPERSONATE = os.environ.get("FRAKBOX_CURL_IMPERSONATE", "safari")


def _needs_rewrite(impersonate):
    """chrome / chrome110 / chromium... -> rewrite. safari, edge, None -> leave."""
    if not impersonate:
        # yfinance always passes an explicit value, but a bare Session() with no
        # impersonate would send a plain-curl fingerprint that the proxy also
        # resets, so pin it to the safe target too.
        return True
    return str(impersonate).lower().startswith("chrom")


def _install_curl_cffi_shim():
    if _DISABLE:
        return
    try:
        from curl_cffi import requests as _cc_requests
    except Exception:
        # curl_cffi not installed / import failure — nothing to patch.
        return

    Session = getattr(_cc_requests, "Session", None)
    if Session is None:
        return

    # Idempotency guard — never double-wrap.
    if getattr(Session, "_frakbox_impersonate_shim", False):
        return

    _orig_init = Session.__init__
    _orig_request = Session.request

    def _patched_init(self, *args, **kwargs):
        if "impersonate" in kwargs and _needs_rewrite(kwargs.get("impersonate")):
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        elif "impersonate" not in kwargs:
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    def _patched_request(self, *args, **kwargs):
        if "impersonate" in kwargs and _needs_rewrite(kwargs.get("impersonate")):
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        return _orig_request(self, *args, **kwargs)

    Session.__init__ = _patched_init
    Session.request = _patched_request
    Session._frakbox_impersonate_shim = True


_install_curl_cffi_shim()
