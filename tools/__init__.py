"""tools package init.

RECURRING INFRA FIX — curl_cffi chrome->safari impersonation shim.

yfinance (>=1.2.0) hardcodes `curl_cffi.requests.Session(impersonate="chrome")`
across base.py/multi.py/scrapers. Behind the agent egress proxy the Chrome TLS
fingerprint fails the handshake (curl 35 / SSLError: Recv failure / OPENSSL
invalid library), while the Safari fingerprint negotiates cleanly (verified:
chrome -> SSLError, safari -> HTTP 200).

This monkeypatch rewrites `impersonate == "chrome"` to `"safari"` at
curl_cffi.requests.Session construction time. It is:
  - idempotent (safe to import many times),
  - narrow (only the exact literal "chrome" is rewritten; versioned Chrome
    profiles like "chrome124" are left untouched),
  - fail-open (if curl_cffi is absent or its API changed, importing tools
    still succeeds).

Importing anything from `tools` (e.g. tools.yfinance_utils) applies the patch.

NOTE: This must live on `main` to survive fresh clones. Until a human merges
it, every fresh clone loses it and reproduces the #1 data_access friction.
"""

_PATCH_FLAG = "_frakbox_chrome_to_safari_patched"


def _apply_curl_cffi_impersonate_shim():
    try:
        import curl_cffi.requests as _ccr
    except Exception:
        return  # curl_cffi not installed / API moved — fail open

    Session = getattr(_ccr, "Session", None)
    if Session is None:
        return

    orig_init = getattr(Session, "__init__", None)
    if orig_init is None or getattr(orig_init, _PATCH_FLAG, False):
        return  # already patched

    def patched_init(self, *args, **kwargs):
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return orig_init(self, *args, **kwargs)

    setattr(patched_init, _PATCH_FLAG, True)
    Session.__init__ = patched_init

    # curl_cffi also exposes AsyncSession; patch it too if present.
    AsyncSession = getattr(_ccr, "AsyncSession", None)
    if AsyncSession is not None:
        a_orig = getattr(AsyncSession, "__init__", None)
        if a_orig is not None and not getattr(a_orig, _PATCH_FLAG, False):
            def patched_async_init(self, *args, **kwargs):
                if kwargs.get("impersonate") == "chrome":
                    kwargs["impersonate"] = "safari"
                return a_orig(self, *args, **kwargs)
            setattr(patched_async_init, _PATCH_FLAG, True)
            AsyncSession.__init__ = patched_async_init


_apply_curl_cffi_impersonate_shim()
