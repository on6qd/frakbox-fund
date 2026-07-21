"""
tools package initializer.

RECURRING-LOSS INFRA SHIM — curl_cffi chrome -> safari impersonation patch.

Why this exists
---------------
yfinance (>= 1.x, confirmed 1.2.0) hardcodes ``curl_cffi.requests.Session(
impersonate="chrome")`` in base.py / data.py / multi.py. Behind this
environment's outbound HTTPS agent proxy, the Chrome TLS fingerprint is reset
by the peer:

    curl: (35) Recv failure: Connection reset by peer   (SSLError)

The Safari fingerprint negotiates cleanly and returns 200 for the same Yahoo
endpoints. Empirically (2026-07-21): chrome -> connection reset, safari /
safari17_0 / safari15_5 / chrome110 -> 200.

This shim monkeypatches ``curl_cffi.requests.Session.__init__`` so that any
session created with ``impersonate="chrome"`` (or with no impersonate at all)
transparently uses ``safari`` instead. It runs at import time of the ``tools``
package, which is imported before any yfinance session is constructed by
``tools.yfinance_utils`` and the scanners.

History / friction
------------------
This file is EMPTY in every fresh clone and has been reconstructed many times
(data_access friction 137x+). The durable fix is to merge it to ``main`` so
fresh clones carry it. Until then, this reconstruction restores price access
for the session.

Idempotent and defensive: if curl_cffi is absent or its API shifts, the shim
degrades silently rather than breaking the import of the whole tools package.
"""

from __future__ import annotations

# Chrome fingerprint is reset by the agent proxy; Safari negotiates cleanly.
_PREFERRED_IMPERSONATE = "safari"


def _install_curl_cffi_safari_shim() -> bool:
    """Patch curl_cffi Session so chrome/absent impersonation -> safari.

    Returns True if the patch was installed, False if it was skipped
    (curl_cffi missing or already patched). Never raises.
    """
    try:
        from curl_cffi import requests as _ccrequests
    except Exception:
        return False

    Session = getattr(_ccrequests, "Session", None)
    if Session is None:
        return False

    # Idempotent: don't double-wrap on re-import.
    if getattr(Session.__init__, "_safari_shim_installed", False):
        return False

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        imp = kwargs.get("impersonate")
        if imp is None or str(imp).lower().startswith("chrome"):
            kwargs["impersonate"] = _PREFERRED_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    _patched_init._safari_shim_installed = True  # type: ignore[attr-defined]
    Session.__init__ = _patched_init  # type: ignore[assignment]
    return True


# Install at package import time (before yfinance builds any session).
_SHIM_INSTALLED = _install_curl_cffi_safari_shim()
