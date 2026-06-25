"""Core guard logic for the PackageGuard proxy — pure functions, no network or
web framework so it is trivially unit-testable.

Flow:
  scan_text(completion) -> GuardResult     (extract names, evaluate each)
  annotation(result)    -> markdown notice to append to an answer
  refine_instruction(r) -> user message that asks the model to regenerate
                           without the flagged packages (self-refinement loop)

The trust-engine `evaluate` function is injected (default = real one) so tests
can pass a stub and avoid any network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from packageguard import extract
from trust_engine.score import evaluate as _real_evaluate

# An evaluator takes (package, ecosystem) and returns an object exposing
# .package, .score, .verdict (the trust_engine.TrustReport shape).
Evaluator = Callable[[str, str], object]


@dataclass
class PackageFinding:
    package: str
    score: float
    verdict: str  # "PASS" | "WARN" | "BLOCK"


@dataclass
class GuardResult:
    findings: list[PackageFinding] = field(default_factory=list)

    @property
    def blocked(self) -> list[str]:
        return [f.package for f in self.findings if f.verdict == "BLOCK"]

    @property
    def warned(self) -> list[str]:
        return [f.package for f in self.findings if f.verdict == "WARN"]

    @property
    def passed(self) -> list[str]:
        return [f.package for f in self.findings if f.verdict == "PASS"]

    @property
    def is_clean(self) -> bool:
        """True when nothing was blocked or warned."""
        return not self.blocked and not self.warned

    @property
    def has_block(self) -> bool:
        return bool(self.blocked)


def scan_text(
    text: str,
    *,
    ecosystem: str = "pypi",
    evaluator: Evaluator | None = None,
) -> GuardResult:
    """Extract package names from `text` and evaluate each via the trust engine."""
    ev = evaluator or _real_evaluate
    names = sorted(extract.extract(text or ""))
    findings: list[PackageFinding] = []
    for name in names:
        rep = ev(name, ecosystem)
        findings.append(
            PackageFinding(package=rep.package, score=float(rep.score), verdict=rep.verdict)
        )
    return GuardResult(findings=findings)


def annotation(result: GuardResult) -> str:
    """Build a Markdown notice describing flagged packages. Empty if clean."""
    if result.is_clean:
        return ""
    lines = ["", "---", "> **\u26a0\ufe0f PackageGuard notice**"]
    for f in result.findings:
        if f.verdict == "BLOCK":
            lines.append(
                f"> - `{f.package}` \u2014 **BLOCKED** (trust score {f.score:.0f}; "
                f"not a trustworthy/known package). Do **not** install it."
            )
        elif f.verdict == "WARN":
            lines.append(
                f"> - `{f.package}` \u2014 **CAUTION** (trust score {f.score:.0f}; "
                f"verify before installing)."
            )
    return "\n".join(lines)


def refine_instruction(result: GuardResult) -> str:
    """Build the follow-up user message that drives the self-refinement loop."""
    flagged = result.blocked + result.warned
    pkgs = ", ".join(f"`{p}`" for p in flagged)
    return (
        "The following package(s) you recommended are not verified as safe/real on "
        f"PyPI and may be hallucinated or risky: {pkgs}. "
        "Please regenerate your answer using only well-established, real packages "
        "that exist on PyPI. If no real package exists for the task, say so plainly "
        "instead of inventing one. "
        "Do NOT mention the flagged package name(s) anywhere in your reply — not in "
        "prose, not in comments, and especially not in any `pip install` command, "
        "even to warn against them. Omit them entirely."
    )
