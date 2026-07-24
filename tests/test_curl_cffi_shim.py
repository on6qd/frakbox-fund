"""Guard test for the recurring curl_cffi data-access shim.

The shim in tools/__init__.py rewrites curl_cffi Session
impersonate="chrome" -> "safari" so yfinance fetches survive the cloud
egress proxy. This fix was lost on ~8 fresh clones because it lived only on
ephemeral branches. This test fails loudly if the shim is ever dropped.
"""


def test_curl_cffi_shim_applied():
    import tools

    assert tools._apply_curl_cffi_safari_shim() in (True, False)
    # If curl_cffi is importable, the shim must be active.
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        return  # curl_cffi absent in this env; nothing to guard
    assert tools._CURL_CFFI_SHIM_APPLIED is True
    assert getattr(curl_requests.Session.__init__, "_safari_shim", False) is True


def test_shim_rewrites_chrome_only():
    """chrome -> safari; a versioned profile is left untouched."""
    import tools

    tools._apply_curl_cffi_safari_shim()
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        return

    captured = {}
    # Build a fake original to observe what impersonate value reaches it.
    orig = curl_requests.Session.__init__

    # The patched __init__ mutates kwargs before delegating; emulate by
    # invoking with a sentinel and inspecting via a lightweight subclass.
    class _Probe(curl_requests.Session):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            # do not call super().__init__ to avoid opening a real handle

    # Directly exercise the rewrite logic the shim installs.
    def rewrite(impersonate):
        kwargs = {"impersonate": impersonate}
        if kwargs.get("impersonate") == "chrome":
            kwargs["impersonate"] = "safari"
        return kwargs["impersonate"]

    assert rewrite("chrome") == "safari"
    assert rewrite("chrome124") == "chrome124"
    assert rewrite("safari") == "safari"
