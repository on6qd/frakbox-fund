#!/usr/bin/env python3
"""Smoke test for the curl_cffi impersonation shim (tools/__init__.py).

Runnable as a plain script (no pytest dependency):

    python tests/test_curl_cffi_shim.py

The agent/execution proxy resets curl_cffi's default "chrome" TLS
impersonation (curl error 35). Safari passes. The shim in tools/__init__.py
rewrites chrome/default impersonation to safari on the shared
`curl_cffi.requests.Session` constructor. This test verifies:

  1. Importing `tools` installs the shim (idempotently).
  2. A Session requested with impersonate="chrome" is rewritten to safari.
  3. The shimmed Session still passes yfinance's isinstance() session guard.

It does NOT hit the network, so it is safe to run offline / in CI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures = []


def check(name, cond, detail=""):
    status = "OK  " if cond else "FAIL"
    if not cond:
        _failures.append(name)
    print(f"  {status} {name}" + (f"  ({detail})" if detail and not cond else ""))


# Importing tools triggers the shim install.
import tools  # noqa: E402

check("tools import installs shim", getattr(tools, "_shim_installed", False) is True)

from curl_cffi import requests as cc_requests  # noqa: E402

check(
    "Session constructor is shimmed",
    getattr(cc_requests.Session, "_frakbox_impersonation_shim", False) is True,
)

# Re-importing / re-installing must be idempotent (no double-wrapping).
first = cc_requests.Session
tools._install_curl_cffi_impersonation_shim()
check("shim install is idempotent", cc_requests.Session is first)

# chrome / default impersonation must be rewritten to safari.
s_chrome = cc_requests.Session(impersonate="chrome")
check("chrome -> safari rewrite", getattr(s_chrome, "impersonate", None) == "safari",
      detail=getattr(s_chrome, "impersonate", None))

s_default = cc_requests.Session()
check("default -> safari rewrite", getattr(s_default, "impersonate", None) == "safari",
      detail=getattr(s_default, "impersonate", None))

# A non-chrome explicit choice is preserved.
s_ff = cc_requests.Session(impersonate="safari17_0")
check("explicit non-chrome preserved", getattr(s_ff, "impersonate", None) == "safari17_0",
      detail=getattr(s_ff, "impersonate", None))

# yfinance guards sessions with isinstance(session, requests.session.Session).
check("passes yfinance isinstance guard",
      isinstance(s_chrome, cc_requests.session.Session))

print(f"\n{'ALL PASSED' if not _failures else 'FAILURES: ' + ', '.join(_failures)}")
sys.exit(1 if _failures else 0)
