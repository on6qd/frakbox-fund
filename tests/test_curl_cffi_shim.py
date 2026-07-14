"""Guard for the recurring curl_cffi chrome->safari shim in tools/__init__.py.

If this test fails, yfinance data access is broken in the cloud environment
(Chrome TLS fingerprint rejected by the egress proxy). See tools/__init__.py.
"""
import tools  # noqa: F401  -- importing the package applies the shim


def test_curl_cffi_shim_applied():
    import curl_cffi.requests as ccr

    assert getattr(ccr.Session, "_frakbox_chrome_to_safari_shim_applied", False), \
        "curl_cffi chrome->safari shim not applied — yfinance will fail in cloud env"


def test_chrome_rewritten_to_safari():
    """The patched __init__ must swap the exact value 'chrome' for 'safari'."""
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome")
    # curl_cffi stores the negotiated impersonation on the session.
    assert getattr(s, "impersonate", None) == "safari"


def test_versioned_profile_untouched():
    import curl_cffi.requests as ccr

    s = ccr.Session(impersonate="chrome124")
    assert getattr(s, "impersonate", None) == "chrome124"
