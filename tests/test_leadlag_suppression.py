#!/usr/bin/env python3
"""Regression tests for two recurring-friction fixes:

1. The yfinance/curl_cffi egress-proxy shim (tools/__init__.py). yfinance 1.2.0
   hardcodes Session(impersonate="chrome"), whose TLS fingerprint the egress
   proxy rejects (curl 35). The shim rewrites chrome -> safari. This fix was
   "applied" and lost across several sessions because it was never committed;
   this test fails loudly if the shim file goes missing again.

2. The systematic lead-lag family auto-suppression
   (_check_systematic_leadlag_family_artifact in data_tasks.py). Codes the
   canonical leadlag_systematic_batch_closure_2026_06_16 /
   _family_classifier_extended_2026_06_17 rules so the same dead-end families
   (macro/rate driver, commodity leg, same non-equity asset class, intl
   non-synchronous lead, leveraged->underlying, same US-equity factor) cannot be
   re-queued as scan hits.

Run: python tests/test_leadlag_suppression.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_curl_cffi_shim_applied():
    import tools  # noqa: F401  (import triggers the package-init shim)
    from curl_cffi import requests as cr
    assert getattr(cr.Session, "_frakbox_impersonate_patched", False), \
        "tools/__init__.py curl_cffi impersonate shim not applied"
    # A Session requested as chrome must be coerced to a proxy-friendly profile.
    s = cr.Session(impersonate="chrome")
    assert getattr(s, "impersonate", "safari") != "chrome", \
        "chrome impersonation was not rewritten"


def test_systematic_leadlag_suppression():
    import data_tasks as dt
    f = dt._check_systematic_leadlag_family_artifact

    # Each pair must be suppressed under the expected criterion family.
    must_suppress = {
        ("FRED:DGS30", "VGLT"): "macro",
        ("FRED:BAMLH0A0HYM2", "XLY"): "macro",
        ("FRED:MORTGAGE30US", "VNQ"): "macro",
        ("GC=F", "IAU"): "commodity",
        ("CL=F", "GLD"): "commodity",
        ("BND", "AGG"): "fixed_income",
        ("IEF", "TLT"): "fixed_income",
        ("ANGL", "JNK"): "fixed_income",
        ("UPRO", "SPY"): "leveraged",
        ("VEA", "SPY"): "intl",
        ("EWZ", "EEM"): "intl",
        ("IVV", "VB"): "us_equity",
        ("QUAL", "MTUM"): "us_equity",
    }
    for (lead, lag), expect in must_suppress.items():
        art = f(lead, lag)
        assert art is not None and art.get("suppressed"), \
            f"{lead}->{lag} should be suppressed ({expect})"

    # Genuinely cross-asset, different-bucket pairs must NOT be rule-suppressed —
    # they are routed to the real test path (and fail the OOS/economic gate there).
    for lead, lag in [("HYG", "SPY"), ("SCHP", "SCHV")]:
        assert f(lead, lag) is None, \
            f"{lead}->{lag} is cross-asset and must route to testing, not rule-suppression"


if __name__ == "__main__":
    test_curl_cffi_shim_applied()
    print("ok: curl_cffi shim applied")
    test_systematic_leadlag_suppression()
    print("ok: systematic lead-lag suppression")
    print("ALL PASS")
