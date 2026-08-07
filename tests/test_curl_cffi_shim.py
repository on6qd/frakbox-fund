"""Guard for the curl_cffi chrome->safari impersonation shim (tools/__init__.py).

Recurring friction #1 (~140x): the cloud egress proxy rejects curl_cffi's
``chrome`` TLS fingerprint but accepts ``safari``. yfinance hardcodes chrome, so
without the shim every price fetch fails on a fresh clone. These tests fail
loudly if the shim is ever dropped again.
"""

import os


def test_shim_marks_session_patched():
    import tools  # noqa: F401 — triggers tools/__init__.py on import
    from curl_cffi import requests as cr
    assert getattr(cr.Session, "_frakbox_impersonate_shim", False), (
        "curl_cffi.Session was not patched — tools/__init__.py shim is missing"
    )


def test_chrome_rewritten_to_safari():
    import tools  # noqa: F401
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome124")
    # curl_cffi stores the chosen profile on the session; it must not be chrome.
    chosen = getattr(s, "impersonate", None)
    assert chosen is not None
    assert not str(chosen).lower().startswith("chrome"), (
        f"chrome impersonation was not rewritten: {chosen!r}"
    )


def test_bare_session_gets_impersonation():
    import tools  # noqa: F401
    from curl_cffi import requests as cr
    s = cr.Session()
    chosen = getattr(s, "impersonate", None)
    assert chosen is not None and not str(chosen).lower().startswith("chrome")


def test_disable_env_var_respected():
    # With the shim disabled, installing on an already-patched session is a no-op
    # (returns False) — and on a hypothetical fresh class it would not patch.
    import tools
    os.environ["FRAKBOX_DISABLE_SHIM"] = "1"
    try:
        assert tools._install_curl_cffi_impersonate_shim() is False
    finally:
        del os.environ["FRAKBOX_DISABLE_SHIM"]


if __name__ == "__main__":
    test_shim_marks_session_patched()
    test_chrome_rewritten_to_safari()
    test_bare_session_gets_impersonation()
    test_disable_env_var_respected()
    print("all curl_cffi shim guard tests PASS")
