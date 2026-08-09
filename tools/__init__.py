"""
tools package init — curl_cffi impersonation shim.

RECURRING INFRA FIX (documented ~140x in the friction log). yfinance hardcodes
`curl_cffi.requests.Session(impersonate="chrome")` in several modules
(base.py, multi.py, scrapers/history.py, data.py). In this execution
environment the outbound agent proxy resets the chrome TLS fingerprint, so every
Yahoo request dies with `curl: (35) Recv failure: Connection reset by peer`.

The `safari` fingerprint negotiates cleanly through the proxy (verified 200 OK).
Importing the `tools` package installs a monkeypatch that rewrites any
`impersonate="chrome*"` request to `safari` at Session construction time, so all
yfinance code paths transparently use a working fingerprint.

This lives in tools/__init__.py so it runs before `import yfinance` anywhere,
because `tools.yfinance_utils` (and friends) trigger this package import first.
"""

from __future__ import annotations

_SAFE_IMPERSONATE = "safari"


def _install_curl_cffi_impersonation_shim() -> None:
    try:
        from curl_cffi import requests as _cc_requests
    except Exception:
        return

    session_cls = getattr(_cc_requests, "Session", None)
    if session_cls is None or getattr(session_cls, "_frakbox_impersonation_shimmed", False):
        return

    _orig_init = session_cls.__init__

    def _patched_init(self, *args, **kwargs):
        imp = kwargs.get("impersonate")
        if imp is None or (isinstance(imp, str) and imp.lower().startswith("chrome")):
            kwargs["impersonate"] = _SAFE_IMPERSONATE
        return _orig_init(self, *args, **kwargs)

    session_cls.__init__ = _patched_init
    session_cls._frakbox_impersonation_shimmed = True

    # Also patch the module-level get/post helpers if present (they build their
    # own ephemeral Session with impersonate="chrome" by default).
    for _fn_name in ("get", "post", "request", "head", "put", "delete", "patch"):
        _fn = getattr(_cc_requests, _fn_name, None)
        if _fn is None or getattr(_fn, "_frakbox_impersonation_shimmed", False):
            continue

        def _make_wrapper(orig):
            def _wrapper(*args, **kwargs):
                imp = kwargs.get("impersonate")
                if imp is None or (isinstance(imp, str) and imp.lower().startswith("chrome")):
                    kwargs["impersonate"] = _SAFE_IMPERSONATE
                return orig(*args, **kwargs)
            _wrapper._frakbox_impersonation_shimmed = True
            return _wrapper

        setattr(_cc_requests, _fn_name, _make_wrapper(_fn))


_install_curl_cffi_impersonation_shim()
