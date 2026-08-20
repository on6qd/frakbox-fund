"""tools package init — network shim for curl_cffi / yfinance.

RECURRING INFRA FIX (see knowledge: tools_init_shim_recurring_loss_*).

Problem
-------
yfinance >= 1.x fetches through curl_cffi with ``impersonate="chrome"``.
In the cloud research environment all outbound HTTPS is tunnelled through a
policy-enforcing egress proxy (HTTPS_PROXY=http://127.0.0.1:45339) that
re-terminates TLS. The proxy RESETS the connection for curl_cffi's default
"chrome" TLS fingerprint:

    curl: (35) Recv failure: Connection reset by peer

but accepts the "safari" (and "chrome110") fingerprints. The proxy also
presents its own CA, so curl_cffi must verify against
/root/.ccr/ca-bundle.crt rather than its bundled roots.

Fix
---
Monkeypatch ``curl_cffi.requests.Session.__init__`` so that:
  * any "chrome*" impersonation is downgraded to a proxy-safe fingerprint,
  * ``verify`` defaults to the proxy CA bundle when present,
and patch the module-level ``curl_cffi.requests.get`` the same way.

This module lives in ``tools/__init__.py`` so importing anything under
``tools`` (all data fetching does) installs the shim automatically. It is
idempotent and a no-op outside the proxied environment.
"""
from __future__ import annotations

import os


def _ca_bundle() -> str | None:
    """Return the proxy CA bundle path if one is configured/exists."""
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        p = os.environ.get(var)
        if p and os.path.exists(p):
            return p
    default = "/root/.ccr/ca-bundle.crt"
    return default if os.path.exists(default) else None


# Fingerprint the egress proxy accepts. "chrome" (curl_cffi default) is reset
# by the proxy; "safari" and "chrome110" pass. Overridable via env for future
# proxy changes without another code loss.
_SAFE_IMPERSONATE = os.environ.get("CURL_CFFI_IMPERSONATE", "safari")


def _proxied() -> bool:
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


def _install_curl_cffi_shim() -> bool:
    try:
        from curl_cffi import requests as _cr
    except Exception:
        return False

    if getattr(_cr, "_frakbox_shim_installed", False):
        return True

    # Only reshape traffic when we are actually behind the re-terminating proxy.
    # Outside it, leave curl_cffi untouched so local runs keep chrome + native CA.
    if not _proxied():
        _cr._frakbox_shim_installed = True
        return True

    ca = _ca_bundle()

    def _fix_kwargs(kwargs: dict) -> dict:
        imp = kwargs.get("impersonate")
        if imp is None or str(imp).startswith("chrome"):
            # downgrade only the reset-prone chrome default; keep explicit
            # non-chrome choices intact.
            if imp is None or imp == "chrome":
                kwargs["impersonate"] = _SAFE_IMPERSONATE
        # set verify to the proxy CA unless caller explicitly disabled it
        if ca and kwargs.get("verify", None) in (None, True):
            kwargs["verify"] = ca
        return kwargs

    # Patch Session.__init__
    _orig_session_init = _cr.Session.__init__

    def _patched_session_init(self, *args, **kwargs):
        _fix_kwargs(kwargs)
        return _orig_session_init(self, *args, **kwargs)

    _cr.Session.__init__ = _patched_session_init

    # Patch AsyncSession if present
    if hasattr(_cr, "AsyncSession"):
        _orig_async_init = _cr.AsyncSession.__init__

        def _patched_async_init(self, *args, **kwargs):
            _fix_kwargs(kwargs)
            return _orig_async_init(self, *args, **kwargs)

        _cr.AsyncSession.__init__ = _patched_async_init

    # Patch module-level convenience functions (get/post/request/head/put)
    for name in ("request", "get", "post", "head", "put", "delete", "patch", "options"):
        orig = getattr(_cr, name, None)
        if orig is None:
            continue

        def _make(orig_fn):
            def _wrapped(*args, **kwargs):
                _fix_kwargs(kwargs)
                return orig_fn(*args, **kwargs)
            return _wrapped

        setattr(_cr, name, _make(orig))

    _cr._frakbox_shim_installed = True
    return True


_install_curl_cffi_shim()
