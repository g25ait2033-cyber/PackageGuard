"""PackageGuard proxy — an OpenAI-compatible middleware that sits between any
client and any LLM backend (local LM Studio / Ollama, or a cloud provider) and
guards against package hallucinations in the model's output.

It exposes POST /v1/chat/completions (the same contract as the OpenAI API), so
pointing an existing OpenAI client at this server is a one-line `base_url` change
and works for local *and* cloud models with no other code changes.

Policies (env PACKAGEGUARD_POLICY):
  * annotate (default) — pass the answer through, append a PackageGuard notice
    listing any BLOCK/WARN packages.
  * block               — same detection, but replace `pip install <bad>` lines in
    the answer with a guard placeholder so the bad command can't be copy-pasted.
  * refine              — self-refinement loop: if anything is flagged, re-prompt
    the SAME model to regenerate without the flagged package(s), up to
    PACKAGEGUARD_MAX_REFINE times, then annotate whatever remains.

Upstream selection (env PACKAGEGUARD_UPSTREAM): "ollama" | "lmstudio" | "cloud".
For "cloud", set PACKAGEGUARD_UPSTREAM_BASE_URL + PACKAGEGUARD_UPSTREAM_API_KEY
(any OpenAI-compatible provider: OpenAI, Groq, Together, OpenRouter, Mistral...).

Run:
  uvicorn packageguard.proxy:app --port 8000
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from packageguard import guard, llm_client

# ----- configuration ---------------------------------------------------------

POLICY = os.getenv("PACKAGEGUARD_POLICY", "annotate").lower()
UPSTREAM = os.getenv("PACKAGEGUARD_UPSTREAM", "ollama").lower()
MAX_REFINE = int(os.getenv("PACKAGEGUARD_MAX_REFINE", "2"))
ECOSYSTEM = os.getenv("PACKAGEGUARD_ECOSYSTEM", "pypi")

# Live demo logging: prints the full prompt -> model -> guard -> refine -> answer
# flow to the proxy's terminal so it can be shown on screen during a demo.
# Disable with PACKAGEGUARD_LOG=0.
LOG_ENABLED = os.getenv("PACKAGEGUARD_LOG", "1") not in ("0", "false", "off", "")
LOG_BODY_LIMIT = int(os.getenv("PACKAGEGUARD_LOG_LIMIT", "1200"))

_log = logging.getLogger("packageguard")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False

_PIP_LINE_RE = re.compile(r"(?im)^(\s*)(pip\s+install\s+.+)$")


def _rule(char: str = "\u2500", width: int = 70) -> str:
    return char * width


def _block(title: str, body: str) -> None:
    """Print a titled block of (possibly long) text to the proxy terminal."""
    if not LOG_ENABLED:
        return
    text = (body or "").strip()
    if len(text) > LOG_BODY_LIMIT:
        text = text[:LOG_BODY_LIMIT].rstrip() + "\n      … (trimmed)"
    _log.info(title)
    for line in text.splitlines():
        _log.info("      %s", line)


def _verdict_summary(result: "guard.GuardResult") -> str:
    parts = []
    for f in result.findings:
        mark = {"BLOCK": "\U0001f6d1", "WARN": "\u26a0\ufe0f", "PASS": "\u2705"}.get(
            f.verdict, "?"
        )
        parts.append(f"{mark} {f.package} {f.verdict} (score {f.score:.0f})")
    return "   ".join(parts) if parts else "(no packages detected)"


def _make_upstream_client():
    if UPSTREAM == "lmstudio":
        return llm_client.make_client()
    if UPSTREAM == "cloud":
        return llm_client.make_client(
            base_url=os.getenv("PACKAGEGUARD_UPSTREAM_BASE_URL"),
            api_key=os.getenv("PACKAGEGUARD_UPSTREAM_API_KEY"),
        )
    return llm_client.make_ollama_client()


# ----- core guarding logic (framework-agnostic) ------------------------------


def _strip_bad_pip_lines(text: str, bad: set[str]) -> str:
    """Replace `pip install ...` lines that reference a flagged package."""
    def repl(m: re.Match) -> str:
        indent, line = m.group(1), m.group(2)
        if any(b in line for b in bad):
            return f"{indent}# [PackageGuard blocked this install command]"
        return m.group(0)

    return _PIP_LINE_RE.sub(repl, text)


def _redact_names(text: str, bad: set[str]) -> str:
    """Redact every standalone mention of a blocked name anywhere in the text.

    The pip-line strip handles install commands; this is the safety net for
    *indirect* references ("you can still use blpd…", comments, backticked
    identifiers) so a stubborn model can't sneak a flagged package back in.
    Word-boundary matching avoids touching legit names that merely contain the
    string (e.g. blocking `heif-python` won't dent `pillow-heif`).
    """
    for name in sorted(bad, key=len, reverse=True):
        if not name:
            continue
        pat = re.compile(rf"(?<![\w.\-]){re.escape(name)}(?![\w.\-])", re.IGNORECASE)
        text = pat.sub("[blocked]", text)
    return text


def guard_completion(
    messages: list[dict],
    completion: str,
    *,
    regenerate: Callable[[list[dict]], str],
    policy: str = POLICY,
    evaluator: guard.Evaluator | None = None,
    max_refine: int = MAX_REFINE,
) -> dict[str, Any]:
    """Apply the guard policy to a completion.

    `regenerate(messages) -> str` is called for the refine loop so this function
    stays free of any specific HTTP client (and is unit-testable with a stub).

    Returns a dict: {content, guard: {...metadata...}}.
    """
    result = guard.scan_text(completion, ecosystem=ECOSYSTEM, evaluator=evaluator)
    refine_rounds = 0

    _block("[1] INITIAL MODEL OUTPUT (raw, from upstream):", completion)
    if LOG_ENABLED:
        _log.info("\n[PackageGuard] SCAN \u2192 %s", _verdict_summary(result))

    # Record each stage so callers can show the full before/after flow.
    history: list[dict[str, Any]] = [
        {
            "stage": "initial",
            "content": completion,
            "blocked": result.blocked,
            "warned": result.warned,
            "passed": result.passed,
        }
    ]

    if policy == "refine" and not result.is_clean:
        convo = list(messages)
        current = completion
        while not result.is_clean and refine_rounds < max_refine:
            instruction = guard.refine_instruction(result)
            if LOG_ENABLED:
                _log.info(
                    "\n[PackageGuard] policy=refine \u2192 re-prompting model to drop: %s",
                    ", ".join(result.blocked + result.warned),
                )
            _block(f"[{refine_rounds + 2}] MIDDLEWARE REFINE PROMPT \u2192 model:", instruction)
            convo = convo + [
                {"role": "assistant", "content": current},
                {"role": "user", "content": instruction},
            ]
            current = regenerate(convo)
            result = guard.scan_text(current, ecosystem=ECOSYSTEM, evaluator=evaluator)
            refine_rounds += 1
            _block(f"[{refine_rounds + 1}] REFINED MODEL OUTPUT:", current)
            if LOG_ENABLED:
                _log.info("\n[PackageGuard] SCAN \u2192 %s", _verdict_summary(result))
            history.append(
                {
                    "stage": f"refine_{refine_rounds}",
                    "content": current,
                    "blocked": result.blocked,
                    "warned": result.warned,
                    "passed": result.passed,
                }
            )
        completion = current

    content = completion
    # Every package blocked across ALL refine rounds, not just the final one — a
    # stubborn model often rephrases or swaps a flagged name between rounds, so
    # the union is what must be kept out of the answer.
    all_blocked = {p for st in history for p in st.get("blocked", [])} | set(result.blocked)
    if policy in ("block", "refine") and all_blocked:
        # 1) drop dangerous `pip install <bad>` lines, then 2) redact any
        # remaining mention of a blocked name anywhere (prose, comments, code)
        # so it can't be referenced "indirectly" after the refine budget is gone.
        content = _strip_bad_pip_lines(content, all_blocked)
        content = _redact_names(content, all_blocked)

    note = guard.annotation(result)
    if note:
        content = content + "\n" + note

    if LOG_ENABLED:
        if refine_rounds and result.is_clean:
            _log.info(
                "\n[PackageGuard] RESOLVED after %d round(s) — model returned a clean answer.",
                refine_rounds,
            )
        elif result.has_block:
            _log.info(
                "\n[PackageGuard] still flagged after %d round(s); stripped dangerous install line(s).",
                refine_rounds,
            )
    _block("[✓] FINAL ANSWER returned to client:", content)
    if LOG_ENABLED:
        _log.info(_rule("\u2550") + "\n")

    return {
        "content": content,
        "guard": {
            "policy": policy,
            "blocked": result.blocked,
            "warned": result.warned,
            "passed": result.passed,
            "refine_rounds": refine_rounds,
            "clean": result.is_clean,
            "history": history,
        },
    }


# ----- FastAPI app -----------------------------------------------------------

app = FastAPI(title="PackageGuard Proxy", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "policy": POLICY, "upstream": UPSTREAM}


@app.get("/v1/models")
def list_models() -> dict:
    try:
        client = _make_upstream_client()
        ids = llm_client.list_models(client)
    except Exception as e:  # upstream down — report empty list, not a 500
        return {"object": "list", "data": [], "error": str(e)}
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}


@app.post("/v1/chat/completions")
def chat_completions(body: dict) -> JSONResponse:
    model = body.get("model")
    messages = body.get("messages", [])
    temperature = body.get("temperature", 1.0)
    max_tokens = body.get("max_tokens", 1024)

    if LOG_ENABLED:
        _log.info("\n" + _rule("\u2550"))
        _log.info("[PackageGuard] NEW REQUEST  model=%s  policy=%s  upstream=%s",
                  model, POLICY, UPSTREAM)
        last_user = next(
            (m.get("content", "") for m in reversed(messages)
             if m.get("role") == "user"),
            "",
        )
        _block("USER PROMPT:", last_user)

    client = _make_upstream_client()

    def call(msgs: list[dict]) -> str:
        out = llm_client.chat(
            client, model=model, messages=msgs,
            temperature=temperature, max_tokens=max_tokens, n=1,
        )
        return out[0] if out else ""

    completion = call(messages)
    guarded = guard_completion(messages, completion, regenerate=call)

    now = int(time.time())
    return JSONResponse(
        {
            "id": f"pkgguard-{now}",
            "object": "chat.completion",
            "created": now,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": guarded["content"]},
                    "finish_reason": "stop",
                }
            ],
            "packageguard": guarded["guard"],
        }
    )
