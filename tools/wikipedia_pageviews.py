"""Wikipedia pageviews fetcher (Wikimedia REST API).

Free daily per-article pageview counts back to 2015-07-01. Useful as a public
attention / retail-interest proxy for the news/sentiment research frontier.

API: https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
        en.wikipedia/all-access/user/{article}/daily/{start}/{end}

`all-access` + `user` (excludes spiders/bots) is the standard attention measure.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

_BASE = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
         "en.wikipedia/all-access/user/{article}/daily/{start}/{end}")
_UA = "frakbox-research/1.0 (causal market research; contact research@example.com)"


def fetch_pageviews(article: str, start: str, end: str,
                    retries: int = 3) -> pd.Series:
    """Daily user pageviews for one article. Dates 'YYYYMMDD'. Returns a Series
    indexed by date (datetime), name=article. Missing days are absent (not 0)."""
    art = urllib.parse.quote(article.replace(" ", "_"), safe="")
    url = _BASE.format(article=art, start=start, end=end)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.load(r).get("items", [])
            idx = pd.to_datetime([it["timestamp"][:8] for it in items],
                                 format="%Y%m%d")
            vals = [it["views"] for it in items]
            return pd.Series(vals, index=idx, name=article, dtype="float64")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch_pageviews failed for {article}: {last}")


def fetch_many(articles: dict[str, str], start: str, end: str,
               pause: float = 0.3) -> pd.DataFrame:
    """articles: {ticker: wiki_article_title}. Returns DataFrame indexed by date,
    columns = tickers (daily user pageviews). Tickers that 404 are skipped."""
    out = {}
    for ticker, title in articles.items():
        try:
            out[ticker] = fetch_pageviews(title, start, end)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {ticker} ({title}): {e}")
        time.sleep(pause)
    return pd.DataFrame(out)


if __name__ == "__main__":
    import sys
    title = sys.argv[1] if len(sys.argv) > 1 else "Apple_Inc."
    s = fetch_pageviews(title, "20240101", "20240131")
    print(s.head(10))
    print(f"mean daily views: {s.mean():.0f}")
