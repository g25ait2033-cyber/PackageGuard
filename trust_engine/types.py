"""Shared types for the trust engine.

Every collector returns a Signal. The scorer combines them with weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    """One trust signal from one collector.

    score:   0 (definitely fake/risky) to 100 (definitely trustworthy).
    weight:  relative importance vs. other signals (set per-collector).
    reasons: short human strings explaining how we got this score.
    raw:     anything the collector wants to keep around for debugging / UI.
    error:   non-empty if the collector failed; score should then be None.
    """

    collector: str
    score: float | None
    weight: float
    reasons: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class TrustReport:
    """Aggregate output for one package."""

    package: str
    ecosystem: str  # "pypi" | "npm"
    score: float            # 0-100 weighted
    verdict: str            # "PASS" | "WARN" | "BLOCK"
    signals: list[Signal] = field(default_factory=list)
