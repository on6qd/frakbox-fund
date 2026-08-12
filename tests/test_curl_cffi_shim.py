"""Guard for the recurring curl_cffi chrome->safari impersonation shim.

If tools/__init__.py is ever lost (the recurring fresh-clone friction), this
test fails loudly instead of every yfinance fetch silently returning no data.
"""

def test_curl_cffi_shim_applied():
    import tools
    assert getattr(tools, "_SHIM_APPLIED", False), (
        "tools/__init__.py curl_cffi impersonation shim did not apply"
    )


def test_chrome_rewritten_to_safari():
    import tools  # ensures shim installed
    import curl_cffi.requests as cr

    assert getattr(cr.Session, "_frakbox_impersonation_shim", False), (
        "curl_cffi Session.__init__ was not patched"
    )

    captured = {}
    sess = cr.Session(impersonate="chrome")
    try:
        captured["impersonate"] = getattr(sess, "impersonate", None)
    finally:
        try:
            sess.close()
        except Exception:
            pass
    # The bare "chrome" profile must have been rewritten away.
    assert captured.get("impersonate") != "chrome"


def test_versioned_chrome_profile_untouched():
    import tools
    import curl_cffi.requests as cr

    # Only the exact bare "chrome" value is rewritten; versioned profiles pass through.
    sess = cr.Session(impersonate="chrome124")
    try:
        assert getattr(sess, "impersonate", None) == "chrome124"
    finally:
        try:
            sess.close()
        except Exception:
            pass
