"""Guard test for the recurring-friction-#1 curl_cffi chrome->safari shim.

If this test ever fails on a fresh clone it means tools/__init__.py (the shim)
was lost again and yfinance will silently fail every fetch in the cloud
egress-proxy environment. See knowledge key
`yfinance_curl_cffi_shim_committed_tools_init_2026_06_27`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_curl_cffi_shim_applied():
    """Importing tools installs the shim; new Sessions default to safari."""
    import tools  # noqa: F401  (triggers package __init__ / shim install)
    from curl_cffi import requests as ccr

    assert getattr(ccr.Session, "_frakbox_curl_cffi_safari_shim_installed", False), (
        "curl_cffi safari shim not installed — tools/__init__.py missing or broken"
    )

    # A Session requesting chrome impersonation must be rewritten to safari.
    s = ccr.Session(impersonate="chrome")
    assert isinstance(s.impersonate, str)
    assert not s.impersonate.lower().startswith("chrome"), (
        f"chrome impersonation not rewritten (got {s.impersonate!r})"
    )


if __name__ == "__main__":
    test_curl_cffi_shim_applied()
    print("PASS: curl_cffi safari shim installed and rewriting chrome->safari")
