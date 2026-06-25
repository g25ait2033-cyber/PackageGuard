"""Name-analysis collector.

Pure heuristic, NO network. ASYMMETRIC VETO signal: a plausible-looking name is
the *default* for a slopsquat, so "the name looks fine" is NOT positive trust.
This collector therefore ABSTAINS (score=None) on a clean name and only votes
(a penalty score) when it actually finds something suspicious:
  - Levenshtein distance 1-2 to a popular package (likely typo-squat).
  - Suspicious chars/patterns (excessive digits, repeated hyphens, leading digit).
  - Length extremes (< 3 or > 40 chars).

When it votes, score is 0 (clearly fake/typo) to ~85 (mild anomaly).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

import Levenshtein

from trust_engine.types import Signal

WEIGHT = 1.0
COLLECTOR_NAME = "nameanalysis"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*[a-z0-9]$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _popular_packages() -> set[str]:
    """Load the canonical popular-packages list bundled with the package."""
    text = resources.files("trust_engine.data").joinpath("popular_pypi.json").read_text(encoding="utf-8")
    return {n.lower() for n in json.loads(text)}


def analyze(package: str, ecosystem: str = "pypi") -> Signal:
    name = package.strip().lower()
    reasons: list[str] = []
    score = 100.0
    penalized = False

    # 1. shape check
    if not name:
        return Signal(COLLECTOR_NAME, 0.0, WEIGHT, ["empty name"], {"name": package})
    if len(name) < 3:
        score -= 40
        penalized = True
        reasons.append(f"very short name ({len(name)} chars)")
    elif len(name) > 40:
        score -= 30
        penalized = True
        reasons.append(f"very long name ({len(name)} chars)")
    if not _NAME_RE.match(name):
        score -= 30
        penalized = True
        reasons.append("name contains unusual characters")

    # 2. content patterns
    digit_ratio = sum(c.isdigit() for c in name) / len(name)
    if digit_ratio > 0.4:
        score -= 25
        penalized = True
        reasons.append(f"high digit ratio ({digit_ratio:.0%})")
    if "--" in name or ".." in name or "__" in name:
        score -= 15
        penalized = True
        reasons.append("repeated separator characters")
    if name[0].isdigit():
        score -= 10
        penalized = True
        reasons.append("name starts with a digit")

    # 3. typo-squat: Levenshtein distance to popular package
    popular = _popular_packages()
    nearest, dist = _nearest_popular(name, popular)
    if name in popular:
        reasons.append("exact match to popular package")
    elif dist == 1:
        score -= 60
        penalized = True
        reasons.append(f"distance 1 from popular package '{nearest}' (typo-squat?)")
    elif dist == 2:
        score -= 35
        penalized = True
        reasons.append(f"distance 2 from popular package '{nearest}'")

    # Clean, plausible name -> abstain. A name with no anomalies is the default
    # state of a fresh squat and must not contribute "free" trust points; let the
    # network signals (registry/maintainer/usage) decide instead.
    if not penalized:
        return Signal(
            collector=COLLECTOR_NAME,
            score=None,
            weight=WEIGHT,
            reasons=["no typo-squat or name anomalies (abstain)"],
            raw={"name": name, "nearest_popular": nearest, "distance": dist, "abstained": True},
        )

    score = max(0.0, min(100.0, score))
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={"name": name, "nearest_popular": nearest, "distance": dist},
    )


def _nearest_popular(name: str, popular: set[str]) -> tuple[str, int]:
    """Return (closest popular name, edit distance). O(N) over the popular list."""
    best_name = ""
    best_dist = 10**9
    for p in popular:
        d = Levenshtein.distance(name, p)
        if d < best_dist:
            best_dist = d
            best_name = p
            if d == 0:
                break
    return best_name, best_dist
