"""Robust EDGAR EFTS (full-text search) fetch helper.

The SEC EFTS endpoint (efts.sec.gov) intermittently returns 5xx errors under
load. Scanners that bail on the first non-200 silently report "0 events", which
is indistinguishable from "no real triggers" — a silent-miss risk for validated
signals awaiting live triggers.

Use efts_get_json() instead of a bare requests.get() so transient 5xx and
connection errors are retried with exponential backoff before giving up.

Created 2026-06-11 after cybersecurity_8k_scanner and activist_13d_scanner both
hit transient `EFTS error 500` during the daily scan, returning 0 events with no
way to distinguish failure from a clean scan.
"""
from __future__ import annotations

import sys
import time

import requests

DEFAULT_HEADERS = {
    "User-Agent": "frakbox-research bart.de.lepeleer@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# 5xx are server-side and worth retrying. 429 = rate limited, also retry.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EFTSFetchError(RuntimeError):
    """Raised when an EFTS request fails after all retries.

    Callers should treat this as DATA-UNAVAILABLE (distinct from a clean scan
    that found zero hits) and avoid concluding "no triggers".
    """


def efts_get_json(
    url: str,
    headers: dict | None = None,
    timeout: int = 30,
    max_retries: int = 5,
    base_delay: float = 1.0,
    label: str | None = None,
) -> dict:
    """GET an EFTS URL and return parsed JSON, retrying transient failures.

    Retries on 429/5xx and connection/timeout errors with exponential backoff
    (base_delay * 2**attempt). Raises EFTSFetchError if all attempts fail so the
    caller can distinguish data-unavailable from an empty result set.
    """
    headers = headers or DEFAULT_HEADERS
    tag = f" [{label}]" if label else ""
    last_err: str | None = None

    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in _RETRYABLE_STATUS:
                last_err = f"HTTP {resp.status_code}"
            else:
                # Non-retryable (e.g. 400 bad query) — fail fast, no point retrying.
                raise EFTSFetchError(f"EFTS non-retryable HTTP {resp.status_code}{tag}: {url}")
        except requests.RequestException as e:
            last_err = type(e).__name__

        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            print(
                f"  EFTS retry{tag} {attempt + 1}/{max_retries - 1} after {last_err} "
                f"(sleep {delay:.0f}s)",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise EFTSFetchError(f"EFTS failed after {max_retries} attempts ({last_err}){tag}: {url}")
