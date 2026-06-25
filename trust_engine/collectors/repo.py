"""Repo collector — pulls GitHub metadata to assess project legitimacy.

Pipeline:
  1. Caller passes the repo URL extracted from PyPI's project_urls (or None).
  2. We parse owner/name out of GitHub URLs.
  3. Fetch repo metadata + last commit timestamp.
  4. Score on stars, age, archived/disabled flag, recency of last commit.

If no repo URL is supplied: score 40 (we can't verify, but legitimate
packages do exist without one — leave it to other signals).
If the URL is non-GitHub: score 60 (we don't currently support GitLab/etc).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 2.0
COLLECTOR_NAME = "repo"

GITHUB_API = "https://api.github.com"
_GH_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/#?]+)", re.IGNORECASE)


def analyze(
    package: str,
    ecosystem: str = "pypi",
    *,
    repo_url: str | None = None,
    _fetch=None,
) -> Signal:
    """Score the repo backing `package`. The registry collector should pass repo_url in."""
    fetch = _fetch or _fetch_github
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")

    if not repo_url:
        return Signal(
            COLLECTOR_NAME, 40.0, WEIGHT,
            ["no repo URL on registry"],
            {"name": name, "repo_url": None},
        )

    parsed = _parse_github(repo_url)
    if not parsed:
        return Signal(
            COLLECTOR_NAME, 60.0, WEIGHT,
            ["repo URL is not a GitHub URL (cannot verify)"],
            {"name": name, "repo_url": repo_url},
        )
    owner, repo = parsed

    try:
        meta = fetch(owner, repo)
    except Exception as e:
        return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name, "owner": owner, "repo": repo}, error=f"fetch failed: {e}")

    if meta is None:
        return Signal(
            COLLECTOR_NAME, 10.0, WEIGHT,
            [f"GitHub repo {owner}/{repo} returns 404"],
            {"name": name, "owner": owner, "repo": repo, "exists": False},
        )

    return _score_repo(name, owner, repo, meta)


def _score_repo(pkg: str, owner: str, repo: str, meta: dict[str, Any]) -> Signal:
    reasons: list[str] = []
    score = 50.0

    if meta.get("archived"):
        score = 20.0
        reasons.append("repo is archived")
    if meta.get("disabled"):
        score = min(score, 15.0)
        reasons.append("repo is disabled")

    stars = int(meta.get("stargazers_count") or 0)
    forks = int(meta.get("forks_count") or 0)
    if stars >= 1000:
        score += 25
        reasons.append(f"{stars} stars")
    elif stars >= 100:
        score += 15
        reasons.append(f"{stars} stars")
    elif stars >= 10:
        score += 5
        reasons.append(f"{stars} stars")
    elif stars == 0:
        score -= 10
        reasons.append("0 stars")
    else:
        reasons.append(f"{stars} stars")

    # age
    age_days = _days_since(meta.get("created_at"))
    if age_days is not None:
        if age_days >= 730:
            score += 10
            reasons.append(f"created {age_days} days ago")
        elif age_days < 30:
            score -= 15
            reasons.append(f"created only {age_days} days ago")

    # last push
    push_age = _days_since(meta.get("pushed_at"))
    if push_age is not None:
        if push_age <= 90:
            score += 10
            reasons.append(f"last push {push_age} days ago")
        elif push_age >= 730:
            score -= 10
            reasons.append(f"last push {push_age} days ago (stale)")

    score = max(0.0, min(100.0, score))
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={
            "name": pkg,
            "owner": owner,
            "repo": repo,
            "exists": True,
            "stars": stars,
            "forks": forks,
            "archived": bool(meta.get("archived")),
            "age_days": age_days,
            "push_age_days": push_age,
        },
    )


def _parse_github(url: str) -> tuple[str, str] | None:
    m = _GH_RE.search(url)
    if not m:
        return None
    name = m.group("name")
    if name.endswith(".git"):
        name = name[:-4]
    return m.group("owner"), name


def _days_since(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def _fetch_github(owner: str, repo: str) -> dict | None:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return http.get_json(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers)
