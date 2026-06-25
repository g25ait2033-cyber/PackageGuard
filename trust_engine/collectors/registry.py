"""Registry collector — does the package exist on PyPI? Is it real and established?

The single strongest hallucination signal is: a package the LLM named that
simply does not exist on PyPI. A non-existent package gets score=0.

For packages that DO exist, score factors:
  - age in days  (older = more trust)
  - release count (more releases = more trust)
  - has Homepage / Source / Repository URL (yes = more trust)
  - latest version is yanked (huge negative)
  - description / README present
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 3.0  # heaviest weight in Phase 1: existence is decisive
COLLECTOR_NAME = "registry"

PYPI_JSON_URL = "https://pypi.org/pypi/{pkg}/json"


def analyze(package: str, ecosystem: str = "pypi", *, _fetch=None) -> Signal:
    """Return a Signal for the package.

    `_fetch` is a test seam: tests pass a fake fetcher to avoid real HTTP.
    """
    fetch = _fetch or _fetch_pypi
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, 0.0, WEIGHT, ["empty name"], {})

    try:
        data = fetch(name)
    except Exception as e:  # network/HTTP error after retries
        return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name}, error=f"fetch failed: {e}")

    if data is None:
        # 404 — package does NOT exist on PyPI. Strongest hallucination signal.
        return Signal(
            collector=COLLECTOR_NAME,
            score=0.0,
            weight=WEIGHT,
            reasons=["package not found on PyPI"],
            raw={"name": name, "exists": False},
        )

    return _score_existing(name, data)


def _score_existing(name: str, data: dict[str, Any]) -> Signal:
    info = data.get("info", {}) or {}
    releases = data.get("releases", {}) or {}
    urls = data.get("urls", []) or []

    reasons: list[str] = ["package exists on PyPI"]
    score = 50.0  # baseline for "exists but unknown quality"

    # 1. release count
    release_count = sum(1 for files in releases.values() if files)
    if release_count >= 10:
        score += 20
        reasons.append(f"{release_count} releases")
    elif release_count >= 3:
        score += 10
        reasons.append(f"{release_count} releases")
    else:
        score -= 10
        reasons.append(f"only {release_count} release(s)")

    # 2. age — first release date
    age_days = _age_days(releases)
    if age_days is not None:
        if age_days >= 365:
            score += 15
            reasons.append(f"first released {age_days} days ago")
        elif age_days >= 90:
            score += 5
            reasons.append(f"first released {age_days} days ago")
        elif age_days < 30:
            score -= 20
            reasons.append(f"very new on PyPI ({age_days} days old)")

    # 3. project URLs
    project_urls = info.get("project_urls") or {}
    has_repo = any(
        any(k.lower().startswith(prefix) for prefix in ("source", "repo", "code", "git"))
        for k in project_urls.keys()
    )
    if has_repo or info.get("home_page"):
        score += 10
        reasons.append("has homepage/repo URL")
    else:
        score -= 10
        reasons.append("no repo or homepage URL")

    # 4. yanked latest release
    if any(f.get("yanked") for f in urls):
        score -= 30
        reasons.append("latest release is yanked")

    # 5. description present
    if not (info.get("description") or info.get("summary")):
        score -= 5
        reasons.append("no description / summary")

    score = max(0.0, min(100.0, score))
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={
            "name": name,
            "exists": True,
            "release_count": release_count,
            "age_days": age_days,
            "version": info.get("version"),
            "yanked": any(f.get("yanked") for f in urls),
            "project_urls": project_urls,
        },
    )


def _age_days(releases: dict[str, list[dict]]) -> int | None:
    """Return days since the first release file was uploaded, or None if unknown."""
    earliest: datetime | None = None
    for files in releases.values():
        for f in files or []:
            ts = f.get("upload_time_iso_8601") or f.get("upload_time")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if earliest is None or dt < earliest:
                earliest = dt
    if earliest is None:
        return None
    return (datetime.now(timezone.utc) - earliest).days


def _fetch_pypi(name: str) -> dict | None:
    return http.get_json(http.pypi_json_url(name))
