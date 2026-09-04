"""tools package init.

PERMANENT FIX for the recurring #1 data_access friction (yfinance TLS handshake
failure behind the egress proxy).

yfinance 1.2.0 hardcodes ``curl_cffi.requests.Session(impersonate="chrome")``
(in base.py / multi.py / scrapers/history.py / data.py). The Chrome TLS
fingerprint fails the policy-enforcing egress proxy handshake — the tunnel is
reset mid-handshake and curl surfaces error 35 ("Recv failure: Connection reset
by peer" / OPENSSL_internal invalid library). The Safari fingerprint negotiates
cleanly (verified: chrome -> reset, safari -> HTTP 200).

Fix: monkeypatch ``curl_cffi.requests.Session.__init__`` so that a request for
the exact ``impersonate="chrome"`` profile is rewritten to ``"safari"``. This is
idempotent, only rewrites the bare ``"chrome"`` value (versioned profiles such
as ``chrome124`` are left untouched), and is applied the moment anything under
``tools/`` is imported — which is the canonical entry point for every data task
(``tools.yfinance_utils`` and friends).

NOTE: this file MUST stay committed on main. It has been silently lost on
several fresh clones because prior fixes were described as committed but never
actually were. Guarded reasoning: verify with ``git log --all -- tools/__init__.py``
that this file is present before recording the friction as fixed.
"""

_CHROME = "chrome"
_SAFARI = "safari"


def _install_curl_cffi_impersonate_shim() -> bool:
    """Rewrite curl_cffi Session impersonate=='chrome' -> 'safari'.

    Returns True if the patch is installed (or already installed), False if
    curl_cffi is unavailable. Safe to call multiple times.
    """
    try:
        from curl_cffi import requests as _cr
    except Exception:
        return False

    Session = _cr.Session

    # Idempotent: don't wrap twice.
    if getattr(Session.__init__, "_ccr_safari_shim", False):
        return True

    _orig_init = Session.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == _CHROME:
            kwargs["impersonate"] = _SAFARI
        return _orig_init(self, *args, **kwargs)

    _patched_init._ccr_safari_shim = True  # type: ignore[attr-defined]
    Session.__init__ = _patched_init
    return True


_SHIM_INSTALLED = _install_curl_cffi_impersonate_shim()
