"""Self-detection collector (optional, opt-in).

Paper RQ3 finding: models detect their OWN hallucinations >75% of the time.
So we can ask the LLM directly: "Is <pkg> a real package on PyPI?"

This is OFF by default (it costs an LLM call). The orchestrator only runs it
when called with use_llm=True, and it is always mocked in unit tests.

Score:
  model says REAL    -> 85
  model says UNSURE  -> 50
  model says FAKE    -> 10
  parse failure      -> error signal (weight 0 contribution)
"""

from __future__ import annotations

import re
from typing import Callable

from trust_engine.types import Signal

WEIGHT = 1.5
COLLECTOR_NAME = "selfcheck"

_SYSTEM = (
    "You are a strict package-registry validator. Answer ONLY with one word: "
    "REAL, FAKE, or UNSURE. REAL means the package definitely exists on the "
    "specified registry. FAKE means it does not exist / you made it up. "
    "UNSURE means you genuinely don't know."
)
_VERDICT_RE = re.compile(r"\b(REAL|FAKE|UNSURE)\b", re.IGNORECASE)

_SCORES = {"REAL": 85.0, "UNSURE": 50.0, "FAKE": 10.0}


def analyze(
    package: str,
    ecosystem: str = "pypi",
    *,
    ask: Callable[[str, str], str] | None = None,
) -> Signal:
    """Ask an LLM whether `package` is real.

    `ask(system_prompt, user_prompt) -> str` is the LLM call. It MUST be
    supplied (by the proxy / bench). If None, we return an error signal so the
    package is never penalised just because no LLM was wired up.
    """
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")
    if ask is None:
        return Signal(
            COLLECTOR_NAME, None, WEIGHT,
            ["no LLM wired up; self-check skipped"],
            {"name": name}, error="no llm",
        )

    registry = {"pypi": "PyPI", "npm": "npm"}.get(ecosystem.lower(), ecosystem)
    user = f"Is the package '{name}' a real, existing package on {registry}? Answer REAL, FAKE, or UNSURE."

    try:
        raw = ask(_SYSTEM, user)
    except Exception as e:
        return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name}, error=f"llm call failed: {e}")

    m = _VERDICT_RE.search(raw or "")
    if not m:
        return Signal(
            COLLECTOR_NAME, None, WEIGHT,
            [f"unparseable LLM answer: {raw[:60]!r}"],
            {"name": name, "raw": raw}, error="unparseable",
        )

    verdict = m.group(1).upper()
    return Signal(
        collector=COLLECTOR_NAME,
        score=_SCORES[verdict],
        weight=WEIGHT,
        reasons=[f"LLM self-assessment: {verdict}"],
        raw={"name": name, "verdict": verdict, "raw": raw},
    )
