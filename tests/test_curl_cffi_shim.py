"""Regression test for the recurring curl_cffi chrome->safari yfinance shim.

The managed egress proxy rejects curl_cffi's Chrome TLS fingerprint (curl 35)
but accepts Safari. `tools/__init__.py` monkeypatches curl_cffi.Session so that
`impersonate="chrome"` is rewritten to `"safari"`. If this file or the shim is
lost in a fresh clone, every yfinance fetch fails. This test fails loudly when
the shim is missing so the loss is caught before data tasks silently break.
"""

import tools  # noqa: F401  -- importing triggers the shim install


def test_curl_cffi_shim_applied():
    from curl_cffi import requests as ccr

    assert getattr(
        ccr.Session.__init__, "_chrome_to_safari_shim", False
    ), "curl_cffi chrome->safari shim not installed on Session.__init__"


def test_chrome_rewritten_to_safari():
    from curl_cffi import requests as ccr

    s = ccr.Session(impersonate="chrome")
    # curl_cffi stores the negotiated impersonation on the session.
    assert getattr(s, "impersonate", None) == "safari"


def test_versioned_profile_untouched():
    from curl_cffi import requests as ccr

    # Only the bare "chrome" value is rewritten; versioned profiles pass through.
    s = ccr.Session(impersonate="chrome124")
    assert getattr(s, "impersonate", None) == "chrome124"
