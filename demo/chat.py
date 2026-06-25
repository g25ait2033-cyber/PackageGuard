"""Interactive PackageGuard demo — a live chat REPL through the proxy.

This is the "show it on screen" demo: you type a coding question, the proxy asks
the local model (Ollama / LM Studio), PackageGuard inspects the answer, and if a
hallucinated / risky package is found you watch the model get asked to rethink
and produce a corrected answer — all printed live in the terminal.

Because the proxy is OpenAI-compatible, this client only needs its base URL.
The actual model runs in Ollama or LM Studio behind the proxy.

Usage (proxy must already be running on :8000):

    python demo/chat.py --model qwen2.5-coder:3b-instruct-q4_K_M

Then just type questions. Examples that often hallucinate on small models:
    - Recommend a Python library to read AVIF images and show the pip install.
    - I need to parse Amharic text in Python. Recommend a library + pip install.
    - Show Python code to read SMART disk health attributes with pip install.

Commands inside the REPL:
    /quit or /exit   leave
    /raw             toggle showing the raw guard JSON
    /full            toggle showing each stage's full model text (default: trimmed)
"""

from __future__ import annotations

import argparse
import sys

import httpx

# ----- tiny ANSI colour helpers (work in modern Windows terminals) -----------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


def _rule(char: str = "\u2500", width: int = 70) -> str:
    return _c(char * width, _DIM)


def _verdict_line(blocked, warned, passed) -> str:
    parts = []
    for p in blocked:
        parts.append(_c(f"\U0001f6d1 {p} BLOCKED", _RED, _BOLD))
    for p in warned:
        parts.append(_c(f"\u26a0\ufe0f  {p} CAUTION", _YELLOW))
    for p in passed:
        parts.append(_c(f"\u2705 {p}", _GREEN))
    return "   ".join(parts) if parts else _c("(no packages detected)", _DIM)


def _trim(text: str, full: bool, limit: int = 600) -> str:
    text = text.strip()
    if full or len(text) <= limit:
        return text
    return text[:limit].rstrip() + _c("\n   … (trimmed; /full to see all)", _DIM)


def _print_stage(stage: dict, full: bool) -> None:
    raw_name = stage.get("stage", "?")
    pretty = {"initial": "INITIAL GENERATION"}.get(
        raw_name, raw_name.replace("_", " ").upper()
    )
    blocked = stage.get("blocked", [])
    warned = stage.get("warned", [])
    passed = stage.get("passed", [])

    header_colour = _RED if blocked else (_YELLOW if warned else _GREEN)
    print()
    print(_c(f"  ┌─ {pretty} ", header_colour, _BOLD))
    print("  │  " + _verdict_line(blocked, warned, passed))
    if blocked:
        print(
            "  │  "
            + _c(
                "→ flagged as hallucinated/untrusted; asking the model to rethink…",
                _MAGENTA,
            )
        )
    body = _trim(stage.get("content", ""), full)
    for ln in body.splitlines():
        print("  │  " + _c(ln, _DIM))
    print(_c("  └" + "\u2500" * 60, _DIM))


def chat_once(base_url, model, messages, temperature, max_tokens, full, show_raw):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = httpx.post(
            f"{base_url}/v1/chat/completions", json=payload, timeout=600
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(_c(f"\n[proxy error] {exc}", _RED))
        return None

    data = resp.json()
    g = data.get("packageguard", {})
    history = g.get("history", [])
    final = data["choices"][0]["message"]["content"]

    print(_rule())
    if history:
        for stage in history:
            _print_stage(stage, full)
    else:
        print(_c("  (older proxy — no per-stage history available)", _DIM))

    # Summary banner
    rounds = g.get("refine_rounds", 0)
    clean = g.get("clean", False)
    blocked = g.get("blocked", [])
    warned = g.get("warned", [])
    print()
    if rounds and clean:
        print(
            _c(
                f"  ✦ Resolved after {rounds} rethink round(s): "
                "model dropped the bad package and returned a clean answer.",
                _GREEN,
                _BOLD,
            )
        )
    elif blocked:
        print(
            _c(
                f"  ✦ Still flagged after {rounds} round(s): {', '.join(blocked)}. "
                "Proxy stripped the dangerous install line as a safety net.",
                _YELLOW,
                _BOLD,
            )
        )
    elif clean:
        print(_c("  ✦ Clean on first pass — no hallucinated packages.", _GREEN, _BOLD))

    print(_rule())
    print(_c("\nAssistant:\n", _CYAN, _BOLD))
    print(final.strip())
    print()

    if show_raw:
        import json

        slim = {k: v for k, v in g.items() if k != "history"}
        print(_c("\n[guard] " + json.dumps(slim), _DIM))

    return final


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PackageGuard interactive demo chat")
    ap.add_argument("--model", required=True, help="model id the proxy should call")
    ap.add_argument("--base-url", default="http://localhost:8000", help="proxy base url")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument(
        "--keep-history",
        action="store_true",
        help="send prior turns back to the model (multi-turn). Default: each "
        "question is independent so demos stay clean.",
    )
    args = ap.parse_args(argv)

    full = False
    show_raw = False

    # check the proxy is up + report its policy
    try:
        h = httpx.get(f"{args.base_url}/healthz", timeout=10).json()
        policy = h.get("policy", "?")
        upstream = h.get("upstream", "?")
    except Exception as exc:  # noqa: BLE001
        print(_c(f"Cannot reach proxy at {args.base_url} — is it running?", _RED))
        print(_c(f"  {exc}", _DIM))
        return 1

    print(_rule("\u2550"))
    print(_c("  PackageGuard live demo", _CYAN, _BOLD))
    print(
        f"  proxy: {args.base_url}   policy: {_c(policy, _BOLD)}   "
        f"upstream: {upstream}   model: {args.model}"
    )
    if policy != "refine":
        print(
            _c(
                "  NOTE: set PACKAGEGUARD_POLICY=refine before starting the proxy "
                "to see the rethink loop.",
                _YELLOW,
            )
        )
    print(
        _c(
            "  Type a coding question. Commands: /quit  /raw  /full",
            _DIM,
        )
    )
    print(_rule("\u2550"))

    convo: list[dict] = []
    while True:
        try:
            user = input(_c("\nYou: ", _MAGENTA, _BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        low = user.lower()
        if low in ("/quit", "/exit", "/q"):
            break
        if low == "/raw":
            show_raw = not show_raw
            print(_c(f"  raw guard JSON: {'on' if show_raw else 'off'}", _DIM))
            continue
        if low == "/full":
            full = not full
            print(_c(f"  full stage text: {'on' if full else 'off'}", _DIM))
            continue

        if args.keep_history:
            convo.append({"role": "user", "content": user})
            messages = convo
        else:
            messages = [{"role": "user", "content": user}]

        final = chat_once(
            args.base_url,
            args.model,
            messages,
            args.temperature,
            args.max_tokens,
            full,
            show_raw,
        )
        if final is not None and args.keep_history:
            convo.append({"role": "assistant", "content": final})

    print(_c("bye.", _DIM))
    return 0


if __name__ == "__main__":
    # enable ANSI on legacy Windows consoles
    if sys.platform == "win32":
        import os

        os.system("")
    raise SystemExit(main())
