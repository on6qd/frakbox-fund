"""
tools package init — curl_cffi chrome->safari impersonation shim.

WHY THIS FILE EXISTS (recurring friction #1, ~145x data_access):
yfinance 1.2.0 hardcodes a curl_cffi ``requests.Session(impersonate="chrome")``
in several internal modules (base.py / multi.py / scrapers / data.py). The
Chrome TLS/JA3 fingerprint is rejected by the outbound egress proxy used in
this environment (handshake dies with curl error 35: "Recv failure: Connection
reset by peer" / OPENSSL_internal). Safari's fingerprint negotiates cleanly.

FIX: monkeypatch curl_cffi so that any request asking to impersonate the exact
value "chrome" is transparently rewritten to a Safari profile. This is:
  * idempotent — applying it twice is a no-op (guarded by a sentinel attr);
  * surgical — ONLY the bare "chrome" value is rewritten. Explicitly versioned
    profiles ("chrome120", "chrome124", "safari17_0", ...) are left untouched;
  * env-overridable — set CURL_CFFI_IMPERSONATE to pick a different target
    (default "safari15_5").

Because ``tools/yfinance_utils.py`` and the standalone scanners live inside
this package, importing anything under ``tools`` runs this module first and
installs the shim before yfinance is used. Modules that ``import yfinance``
directly (e.g. nt_filing_scanner.py) should ``import tools`` at the top to
self-trigger the patch.

If this file is missing on a fresh clone, every yfinance fetch fails. It has
been lost repeatedly because it lived only on session branches — it must be
merged to main.
"""

from __future__ import annotations

import os

_TARGET = os.environ.get("CURL_CFFI_IMPERSONATE", "safari15_5")
_SENTINEL = "_frakbox_chrome_to_safari_shim"


def _rewrite(value):
    """Rewrite the bare 'chrome' impersonate value to the Safari target.

    Only the exact string 'chrome' is rewritten; versioned/other profiles pass
    through unchanged.
    """
    if value == "chrome":
        return _TARGET
    return value


def _install_curl_cffi_shim() -> bool:
    try:
        import curl_cffi.requests as ccr
    except Exception:
        return False

    patched_any = False

    for cls_name in ("Session", "AsyncSession"):
        cls = getattr(ccr, cls_name, None)
        if cls is None:
            continue
        if getattr(cls, _SENTINEL, False):
            patched_any = True
            continue

        # Patch __init__ so a session created with impersonate="chrome" is
        # rewritten at construction time.
        orig_init = cls.__init__

        def make_init(_orig_init):
            def __init__(self, *args, **kwargs):
                if kwargs.get("impersonate") == "chrome":
                    kwargs["impersonate"] = _TARGET
                return _orig_init(self, *args, **kwargs)

            return __init__

        cls.__init__ = make_init(orig_init)

        # Patch request() so a per-call impersonate="chrome" is also rewritten.
        orig_request = getattr(cls, "request", None)
        if orig_request is not None:

            def make_request(_orig_request):
                def request(self, *args, **kwargs):
                    if kwargs.get("impersonate") == "chrome":
                        kwargs["impersonate"] = _TARGET
                    return _orig_request(self, *args, **kwargs)

                return request

            cls.request = make_request(orig_request)

        setattr(cls, _SENTINEL, True)
        patched_any = True

    # Module-level helpers (curl_cffi.requests.get/post/request) also accept an
    # impersonate kwarg. Patch the ones that exist.
    for fn_name in ("request", "get", "post", "head", "put", "delete", "patch", "options"):
        fn = getattr(ccr, fn_name, None)
        if fn is None or getattr(fn, _SENTINEL, False):
            continue

        def make_fn(_fn):
            def wrapper(*args, **kwargs):
                if kwargs.get("impersonate") == "chrome":
                    kwargs["impersonate"] = _TARGET
                return _fn(*args, **kwargs)

            setattr(wrapper, _SENTINEL, True)
            return wrapper

        try:
            setattr(ccr, fn_name, make_fn(fn))
        except Exception:
            pass

    return patched_any


_SHIM_INSTALLED = _install_curl_cffi_shim()
