"""
Guard test for the curl_cffi Chrome->Safari impersonation shim (tools/__init__.py).

The shim exists because yfinance 1.2.0 hardcodes impersonate="chrome", whose
TLS fingerprint is rejected by the egress proxy (curl 35). Importing `tools`
must rewrite that to a Safari profile. If this test disappears or fails on a
fresh clone, yfinance data access is broken again — see the recurring #1
data_access friction in the research journal.
"""
import os


def test_shim_applied_flag():
    import tools
    assert tools._applied is True


def test_session_init_rewrites_chrome():
    import tools  # noqa: F401 (self-triggers the patch)
    from curl_cffi import requests as cc

    captured = {}
    # Build a Session asking for chrome; the shim should have rewritten it.
    s = cc.Session(impersonate="chrome")
    # curl_cffi stores the negotiated impersonate on the session.
    val = getattr(s, "impersonate", None)
    captured["impersonate"] = val
    assert val != "chrome", f"chrome was not rewritten (got {val!r})"
    assert val == os.environ.get("CURL_CFFI_IMPERSONATE", "safari15_5")


def test_versioned_chrome_untouched():
    import tools  # noqa: F401
    from curl_cffi import requests as cc

    s = cc.Session(impersonate="chrome110")
    assert getattr(s, "impersonate", None) == "chrome110"


def test_idempotent_reapply():
    import tools
    # Re-applying must not stack patches or raise.
    tools._apply_curl_cffi_shim()
    from curl_cffi import requests as cc

    init = cc.Session.__init__
    assert getattr(init, "_chrome_safari_shim", False) is True


if __name__ == "__main__":
    import traceback

    tests = [
        test_shim_applied_flag,
        test_session_init_rewrites_chrome,
        test_versioned_chrome_untouched,
        test_idempotent_reapply,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} PASS")
    raise SystemExit(0 if passed == len(tests) else 1)
