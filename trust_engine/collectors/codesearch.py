"""Code-search collector — counts public GitHub repos that import the package.

Uses the GitHub code-search REST endpoint:
  GET /search/code?q="import <pkg>"+language:Python

This is the strongest "this package exists and is used" signal we have.
A package with zero `import` hits anywhere on GitHub is overwhelmingly
likely to be a hallucinated name.

NOTE: GitHub code-search REQUIRES authentication. Without GITHUB_TOKEN
in env, the call returns 422. We return an error Signal in that case.

PyPI distribution name vs. Python import name can differ
(e.g. `beautifulsoup4` -> `bs4`). Callers may pass `import_name=` to
override. Default: replace '-' with '_'.

Score (log-scale):
   0 hits   -> 20   (very likely hallucinated)
   1-9      -> 45
  10-99     -> 65
 100-999    -> 80
 >=1000     -> 95
"""

from __future__ import annotations

import os
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 2.0
COLLECTOR_NAME = "codesearch"
GITHUB_SEARCH_URL = "https://api.github.com/search/code"


def analyze(
    package: str,
    ecosystem: str = "pypi",
    *,
    import_name: str | None = None,
    _fetch=None,
) -> Signal:
    fetch = _fetch or _fetch_github
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")

    imp = (import_name or name.replace("-", "_")).strip()
    query = f'"import {imp}" language:Python'

    if not _fetch and not os.getenv("GITHUB_TOKEN"):
        return Signal(
            COLLECTOR_NAME, None, WEIGHT,
            ["GITHUB_TOKEN not set; code-search requires auth"],
            {"name": name, "import_name": imp},
            error="missing GITHUB_TOKEN",
        )

    try:
        data = fetch(query)
    except Exception as e:
        return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name, "import_name": imp}, error=f"fetch failed: {e}")

    total = int((data or {}).get("total_count") or 0)
    score = _score_from_count(total)
    reasons = [f"{total} public Python files import '{imp}'"]
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={"name": name, "import_name": imp, "total_count": total, "query": query},
    )


def _score_from_count(n: int) -> float:
    if n <= 0:
        return 20.0
    if n < 10:
        return 45.0
    if n < 100:
        return 65.0
    if n < 1000:
        return 80.0
    return 95.0


def _fetch_github(query: str) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_SEARCH_URL}?q={_urlquote(query)}&per_page=1"
    # We only care about total_count, so per_page=1 keeps response tiny.
    return http.get_json(url, headers=headers) or {}


def _urlquote(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s)
