"""Trust-engine orchestrator.

Public API: `evaluate(package, ecosystem)` -> TrustReport

Pipeline:
  1. nameanalysis   (no network)
  2. registry       (1 PyPI JSON call, cached & reused)
  3. osv            (1 OSV POST)
  4. maintainer     (reuses registry's info; no extra HTTP)
  5. repo           (1 GitHub repos call if URL present)
  6. installscan    (1 extra PyPI JSON only if registry's URLs aren't reused)
  7. codesearch     (1 GitHub code-search if GITHUB_TOKEN set)

We deliberately tolerate per-collector failures: a collector that errors
out contributes weight=0 to the final score (it just doesn't vote).
"""

from __future__ import annotations

from typing import Iterable

from typing import Callable

from trust_engine import cache
from trust_engine.collectors import (
    codesearch,
    installscan,
    maintainer,
    nameanalysis,
    osv,
    registry,
    repo,
    selfcheck,
)
from trust_engine.types import Signal, TrustReport

PASS_THRESHOLD = 70.0
WARN_THRESHOLD = 40.0


def evaluate(
    package: str,
    ecosystem: str = "pypi",
    *,
    use_cache: bool = True,
    llm_ask: Callable[[str, str], str] | None = None,
) -> TrustReport:
    """Run all collectors and produce a TrustReport.

    If `llm_ask` is provided, the optional self-detection signal runs (paper RQ3).
    Caching is skipped when llm_ask is used, since the answer is model-dependent.
    """
    pkg = package.strip()
    if not pkg:
        return TrustReport(
            package=package, ecosystem=ecosystem,
            score=0.0, verdict="BLOCK",
            signals=[Signal("input", 0.0, 1.0, ["empty package name"], {}, error="empty")],
        )

    use_cache = use_cache and llm_ask is None
    if use_cache:
        cached = cache.get(pkg, ecosystem)
        if cached is not None:
            return cached

    signals: list[Signal] = []

    # 1. name analysis
    signals.append(nameanalysis.analyze(pkg, ecosystem))

    # 2. registry — fetched once, shared with maintainer + installscan
    reg_sig = registry.analyze(pkg, ecosystem)
    signals.append(reg_sig)
    registry_info = _info_from_registry(reg_sig)
    registry_urls = (reg_sig.raw or {}).get("project_urls") or {}
    repo_url = _pick_repo_url(registry_urls)

    # 3. OSV
    signals.append(osv.analyze(pkg, ecosystem))

    # 4. maintainer (reuses info — no HTTP)
    signals.append(maintainer.analyze(pkg, ecosystem, info=registry_info))

    # 5. repo (only if registry says it exists)
    if reg_sig.raw and reg_sig.raw.get("exists"):
        signals.append(repo.analyze(pkg, ecosystem, repo_url=repo_url))
    else:
        signals.append(
            Signal("repo", 0.0, repo.WEIGHT, ["skipped: package not on registry"], {})
        )

    # 6. installscan
    if reg_sig.raw and reg_sig.raw.get("exists"):
        signals.append(installscan.analyze(pkg, ecosystem))
    else:
        signals.append(
            Signal("installscan", 0.0, installscan.WEIGHT, ["skipped: package not on registry"], {})
        )

    # 7. codesearch
    signals.append(codesearch.analyze(pkg, ecosystem))

    # 8. self-detection (optional, only when an LLM is wired up)
    if llm_ask is not None:
        signals.append(selfcheck.analyze(pkg, ecosystem, ask=llm_ask))

    score = _weighted_score(signals)
    verdict = _verdict(score, signals)
    report = TrustReport(package=pkg, ecosystem=ecosystem, score=score, verdict=verdict, signals=signals)

    if use_cache:
        cache.put(report)
    return report


def _info_from_registry(reg_sig: Signal) -> dict | None:
    # registry's raw omits the full info block, so maintainer fetches its own.
    return None


def _pick_repo_url(project_urls: dict) -> str | None:
    if not project_urls:
        return None
    # Look for github first
    for k, v in project_urls.items():
        if isinstance(v, str) and "github.com" in v.lower():
            return v
    # Fallback: anything that looks repo-y
    for k, v in project_urls.items():
        if not isinstance(v, str):
            continue
        kl = k.lower()
        if kl.startswith(("source", "repo", "code", "git")):
            return v
    return None


def _weighted_score(signals: Iterable[Signal]) -> float:
    total_w = 0.0
    total = 0.0
    for s in signals:
        if s.score is None or s.error:
            continue
        total += s.score * s.weight
        total_w += s.weight
    if total_w == 0:
        return 0.0
    return round(total / total_w, 1)


def _verdict(score: float, signals: list[Signal]) -> str:
    # Hard-block: registry says package doesn't exist
    for s in signals:
        if s.collector == "registry" and s.raw and s.raw.get("exists") is False:
            return "BLOCK"
    if score >= PASS_THRESHOLD:
        return "PASS"
    if score >= WARN_THRESHOLD:
        return "WARN"
    return "BLOCK"
