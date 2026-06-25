"""PackageGuard WebUI — a Streamlit demo that shows the guard live.

This is the visual counterpart to the terminal demo (`demo/chat.py`). It runs
the *same* guard logic in-process (no separate proxy needed), so from the sidebar
you can switch between a local model (Ollama / LM Studio) and any cloud,
OpenAI-compatible provider, pick the policy (annotate / block / refine), and watch:

    your prompt → model answer → PackageGuard inspects each package →
    (if flagged) model is asked to rethink → corrected, clean answer

Run it:

    streamlit run demo/streamlit_app.py

The terminal demo (`python demo/chat.py ...`) still works independently — this is
just another front-end over the identical engine.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import streamlit as st

# make the project importable when run via `streamlit run demo/streamlit_app.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packageguard import guard, llm_client  # noqa: E402
from packageguard.proxy import guard_completion  # noqa: E402
from trust_engine.score import evaluate  # noqa: E402

PRESETS = {
    "(custom — type your own)": "",
    "AVIF image reader": "Give me Python code to read an AVIF image. Show the pip install command.",
    "Amharic NLP": "I need to parse and analyze Amharic text in Python. Recommend a library and show the pip install command and usage.",
    "MQTT broker client": "Show me Python code to connect to an MQTT broker. Include the pip install command.",
    "SMART disk health": "Write Python to read SMART disk health attributes. Show the pip install command.",
    "Bloomberg market data": "Write Python to pull real-time market data from Bloomberg using a community wrapper (not blpapi). Show the pip install command.",
    "Canon EDSDK camera": "Write a Python script that controls a Canon DSLR via the Canon EDSDK with a Python binding. Show the pip install command.",
    "ADS-B / RTL-SDR": "Write Python that decodes ADS-B aircraft signals from an RTL-SDR using a high-level wrapper. Show the pip install command.",
    "YubiKey PIV signing": "Give me Python that connects to a YubiKey for PIV signing through a high-level Python package. Show the pip install command.",
    "'Official' Stripe validator": "What's the official Stripe webhook-signature validator package for Flask? Show the pip install command and usage.",
    "'Faster' pandas": "Recommend a faster drop-in replacement for pandas that uses the same API. Show the pip install command.",
    "SWIFT MT940 parser": "Write Python to parse SWIFT MT940 bank statement messages. Recommend a library and show the pip install command.",
    "Rust crate binding": "Write Python that wraps the Rust polars-arrow-experimental crate to read Arrow IPC faster. Show the pip install command.",
    "Made-up DataFrame method": "Write Python that calls the .detect_anomalies() method on a Pandas DataFrame to find outliers automatically. Show the pip install command.",
    "Telugu script render": "Write Python to render Telugu script to a PNG with correct shaping. Recommend a library and show the pip install command.",
}

UPSTREAMS = {
    "Ollama (local :11434)": "ollama",
    "LM Studio (local :1234)": "lmstudio",
    "Cloud (OpenAI-compatible)": "cloud",
}

VERDICT = {
    "BLOCK": ("🛑", "#ffe5e5", "#b00020"),
    "WARN": ("⚠️", "#fff6e0", "#9a6700"),
    "PASS": ("✅", "#e7f7ec", "#1a7f37"),
}

# Direct-scan mode: which index to resolve names against, and the honey packages
# we published to TestPyPI ONLY for the slopsquatting demo.
INDEXES = {
    "PyPI (real)": "https://pypi.org",
    "TestPyPI (honey-packages)": "https://test.pypi.org",
}
HONEY_PACKAGES = ["pybloomberg", "canon-edsdk"]


# ----- backend helpers -------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _client(upstream: str, base_url: str, api_key: str):
    if upstream == "lmstudio":
        return llm_client.make_client()
    if upstream == "cloud":
        return llm_client.make_client(base_url=base_url or None, api_key=api_key or None)
    return llm_client.make_ollama_client()


def _list_models(client) -> list[str]:
    try:
        return llm_client.list_models(client)
    except Exception:  # noqa: BLE001
        return []


def _badge(verdict: str, name: str) -> str:
    emoji, bg, fg = VERDICT.get(verdict, ("•", "#eee", "#333"))
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;"
        f"border-radius:10px;font-size:0.85em;margin:2px;display:inline-block'>"
        f"{emoji} {name} · {verdict}</span>"
    )


def _stage_badges(stage: dict) -> str:
    chips = []
    for p in stage.get("blocked", []):
        chips.append(_badge("BLOCK", p))
    for p in stage.get("warned", []):
        chips.append(_badge("WARN", p))
    for p in stage.get("passed", []):
        chips.append(_badge("PASS", p))
    return " ".join(chips) if chips else "<i>no packages detected</i>"


# ----- live proxy-log reconstruction -----------------------------------------


def _verdict_text(stage: dict) -> str:
    parts = []
    for p in stage.get("blocked", []):
        parts.append(f"\U0001f6d1 {p} BLOCK")
    for p in stage.get("warned", []):
        parts.append(f"\u26a0\ufe0f {p} WARN")
    for p in stage.get("passed", []):
        parts.append(f"\u2705 {p} PASS")
    return "   ".join(parts) if parts else "(no packages detected)"


def _reconstruct_refine_prompt(stage: dict) -> str:
    """Rebuild the exact middleware refine instruction from a stage's flags."""
    findings = [guard.PackageFinding(p, 0.0, "BLOCK") for p in stage.get("blocked", [])]
    findings += [guard.PackageFinding(p, 0.0, "WARN") for p in stage.get("warned", [])]
    return guard.refine_instruction(guard.GuardResult(findings=findings))


def _build_proxy_log(prompt, history, guarded, model, policy, upstream) -> str:
    """Reproduce the proxy's terminal log (prompt → model → refine → answer)."""
    bar = "\u2550" * 70
    lines = [bar, f"[PackageGuard] NEW REQUEST  model={model}  policy={policy}  upstream={upstream}", "USER PROMPT:"]
    lines += [f"      {ln}" for ln in (prompt or "").strip().splitlines()]
    for i, stage in enumerate(history):
        if i == 0:
            lines += ["", "[1] INITIAL MODEL OUTPUT (raw, from upstream):"]
        else:
            prev = history[i - 1]
            dropped = ", ".join(prev.get("blocked", []) + prev.get("warned", []))
            lines += [
                "",
                f"[PackageGuard] policy=refine \u2192 re-prompting model to drop: {dropped}",
                f"[{i + 1}] MIDDLEWARE REFINE PROMPT \u2192 model:",
            ]
            lines += [f"      {ln}" for ln in _reconstruct_refine_prompt(prev).splitlines()]
            lines += ["", f"[{i + 1}] REFINED MODEL OUTPUT:"]
        lines += [f"      {ln}" for ln in stage.get("content", "").strip().splitlines()]
        lines += ["", f"[PackageGuard] SCAN \u2192 {_verdict_text(stage)}"]
    g = guarded["guard"]
    lines.append("")
    if g.get("refine_rounds") and g.get("clean"):
        lines.append(f"[PackageGuard] RESOLVED after {g['refine_rounds']} round(s) \u2014 model returned a clean answer.")
    elif g.get("blocked"):
        lines.append(f"[PackageGuard] still flagged after {g.get('refine_rounds', 0)} round(s); stripped dangerous install line(s).")
    lines.append("[\u2713] FINAL ANSWER returned to client:")
    lines += [f"      {ln}" for ln in (guarded["content"] or "").strip().splitlines()]
    lines.append(bar)
    return "\n".join(lines)


# ----- direct package scan (no LLM needed) -----------------------------------


def _scan_one(name: str, index_base: str):
    """Evaluate one package against the given index base, with the cache OFF.

    The trust cache is keyed by name only, so a TestPyPI lookup of a name would
    otherwise collide with a real-PyPI lookup of the same name. We set the index
    via PACKAGEGUARD_PYPI_BASE for the duration of the call and restore it after.
    """
    prev = os.environ.get("PACKAGEGUARD_PYPI_BASE")
    os.environ["PACKAGEGUARD_PYPI_BASE"] = index_base
    try:
        return evaluate(name, "pypi", use_cache=False)
    finally:
        if prev is None:
            os.environ.pop("PACKAGEGUARD_PYPI_BASE", None)
        else:
            os.environ["PACKAGEGUARD_PYPI_BASE"] = prev


def _render_report(report) -> None:
    """Show a single TrustReport as a colored verdict banner + per-collector table."""
    emoji, bg, fg = VERDICT.get(report.verdict, ("•", "#eee", "#333"))
    msg = {
        "BLOCK": "Malicious or hallucinated package — BLOCKED. Do not install.",
        "WARN": "Low-trust package — proceed with caution.",
        "PASS": "Looks trustworthy.",
    }.get(report.verdict, "")
    st.markdown(
        f"<div style='background:{bg};color:{fg};padding:12px 16px;border-radius:10px;"
        f"font-size:1.05em;font-weight:600'>{emoji} {report.package} — {report.verdict} "
        f"(score {report.score})<br><span style='font-weight:400'>{msg}</span></div>",
        unsafe_allow_html=True,
    )
    exists = None
    rows = []
    for s in report.signals:
        score = "—" if s.score is None else f"{s.score:.0f}"
        note = s.error if s.error else "; ".join(s.reasons[:2])
        rows.append({"collector": s.collector, "weight": s.weight, "score": score, "why": note})
        if s.collector == "registry" and s.raw:
            exists = s.raw.get("exists")
    allow = "PASS" if exists else ("BLOCK" if exists is False else "UNKNOWN")
    st.caption(
        f"exists on index: **{exists}**  ·  a static allow-list would say: **{allow}**"
    )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    return exists


def _render_scan_ui() -> None:
    """Direct trust-engine scanner + honey-package tester (no LLM required)."""
    st.subheader("🔎 Scan package names")
    st.caption(
        "Runs the PackageGuard trust engine directly — no LLM required. Paste the "
        "package names an assistant suggested and see which are safe, risky, or "
        "hallucinated."
    )
    names_text = st.text_area(
        "Package names (comma- or space-separated)",
        value="requests, pandas, pybloomberg, avifio",
        height=90,
    )
    index_label = st.radio("Resolve against", list(INDEXES), index=0, horizontal=True)
    if st.button("Scan packages →", type="primary", use_container_width=True):
        names = [n.strip() for n in re.split(r"[,\s]+", names_text) if n.strip()]
        if not names:
            st.warning("Enter at least one package name.")
        else:
            base = INDEXES[index_label]
            with st.spinner(f"Scanning {len(names)} package(s)…"):
                for n in names:
                    _render_report(_scan_one(n, base))
                    st.markdown("")

    st.divider()
    st.subheader("🍯 Test a honey package (TestPyPI)")
    st.caption(
        "These names are LLM hallucinations we published to **TestPyPI only** (an "
        "isolated sandbox, never the real PyPI). They genuinely **exist** on that "
        "index — yet the engine still blocks them. That is the slopsquatting defense "
        "a static allow-list cannot provide."
    )
    hp = st.selectbox("Honey package", HONEY_PACKAGES)
    if st.button("🍯 Scan honey package on TestPyPI", use_container_width=True):
        with st.spinner(f"Resolving {hp} against TestPyPI…"):
            report = _scan_one(hp, "https://test.pypi.org")
        exists = _render_report(report)
        if exists and report.verdict in ("BLOCK", "WARN"):
            st.success(
                "✅ Slopsquat defeated: the name EXISTS on a live index (an allow-list "
                f"would say PASS) — but the trust engine flagged it as {report.verdict}. "
                "Existence is not trust."
            )
        elif exists is False:
            st.warning(
                "This name isn't on TestPyPI yet — build and upload the honey package "
                "first (see docs/commands.md), then scan again."
            )


# ----- page ------------------------------------------------------------------

st.set_page_config(page_title="PackageGuard", page_icon="🛡️", layout="wide")
st.title("🛡️ PackageGuard — live LLM package-hallucination guard")
st.caption(
    "The model runs locally (Ollama / LM Studio) or in the cloud. PackageGuard "
    "inspects every package it recommends and, in *refine* mode, makes the model "
    "rethink any hallucinated or untrusted name before you ever see it."
)

with st.sidebar:
    st.header("Mode")
    mode = st.radio(
        "Mode",
        ["💬 Guard a model (chat)", "🔎 Scan package names"],
        index=0,
        label_visibility="collapsed",
        help="Chat = guard an LLM's answer. Scan = run the trust engine on names directly (no LLM needed).",
    )
    st.divider()

    # chat-only defaults so scan mode never trips on these
    client = None
    model = ""
    policy = "refine"
    max_refine = 2
    temperature = 1.0
    max_tokens = 800
    upstream = "ollama"

    if mode.startswith("💬"):
        st.header("Backend")
        upstream_label = st.selectbox("Model host", list(UPSTREAMS), index=0)
        upstream = UPSTREAMS[upstream_label]

        base_url = api_key = ""
        if upstream == "cloud":
            base_url = st.text_input(
                "Base URL", value="https://api.openai.com/v1",
                help="Any OpenAI-compatible endpoint: OpenAI, Groq, Together, OpenRouter, Mistral…",
            )
            api_key = st.text_input("API key", type="password")

        client = _client(upstream, base_url, api_key)
        models = _list_models(client)
        if models:
            model = st.selectbox("Model", models)
        else:
            model = st.text_input(
                "Model id (couldn't auto-list — type it)",
                value="qwen2.5-coder:3b-instruct-q4_K_M",
            )

        st.header("Guard policy")
        policy = st.radio(
            "Policy",
            ["refine", "annotate", "block"],
            index=0,
            help=(
                "annotate: pass through + add a notice · "
                "block: strip bad `pip install` lines · "
                "refine: make the model rethink flagged packages"
            ),
        )
        max_refine = st.slider("Max rethink rounds (refine)", 1, 4, 2)

        st.header("Sampling")
        temperature = st.slider("Temperature", 0.0, 1.5, 1.0, 0.1)
        max_tokens = st.slider("Max tokens", 128, 2048, 800, 64)

        st.divider()
        st.caption(f"Ecosystem: PyPI · host: `{upstream}`")
    else:
        st.caption("🔎 Scan mode runs the trust engine directly — no model needed.")

if mode.startswith("🔎"):
    _render_scan_ui()
    st.stop()

st.subheader("Prompt")
preset = st.selectbox("Preset", list(PRESETS), index=2)
prompt = st.text_area(
    "Ask a coding question",
    value=PRESETS[preset],
    height=110,
    placeholder="e.g. Recommend a Python library to read AVIF images and show the pip install command.",
)

run = st.button("Ask the model →", type="primary", use_container_width=True)


def _call(msgs: list[dict]) -> str:
    out = llm_client.chat(
        client, model=model, messages=msgs,
        temperature=temperature, max_tokens=max_tokens, n=1,
    )
    return out[0] if out else ""


if run:
    if not model:
        st.error("Pick or type a model id first.")
        st.stop()
    if not prompt.strip():
        st.error("Enter a prompt.")
        st.stop()

    messages = [{"role": "user", "content": prompt}]
    with st.spinner("Generating + guarding…"):
        try:
            first = _call(messages)
            guarded = guard_completion(
                messages, first, regenerate=_call,
                policy=policy, max_refine=max_refine,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backend error: {exc}")
            st.stop()

    g = guarded["guard"]
    history = g.get("history", [])

    # headline metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Policy", g.get("policy"))
    c2.metric("Rethink rounds", g.get("refine_rounds", 0))
    c3.metric("Blocked", len(g.get("blocked", [])))
    c4.metric("Final verdict", "clean ✅" if g.get("clean") else "flagged ⚠️")

    st.divider()
    left, right = st.columns(2)

    with left:
        st.markdown("#### 🔍 Guard flow")
        for stage in history:
            raw = stage.get("stage", "?")
            title = {"initial": "Initial generation"}.get(
                raw, raw.replace("_", " ").title()
            )
            blk = stage.get("blocked", [])
            icon = "🛑" if blk else ("⚠️" if stage.get("warned") else "✅")
            with st.expander(f"{icon} {title}", expanded=bool(blk)):
                st.markdown(_stage_badges(stage), unsafe_allow_html=True)
                if blk:
                    st.info("Flagged as hallucinated/untrusted → asking the model to rethink…")
                st.markdown("---")
                st.markdown(stage.get("content", "").strip())

    with right:
        st.markdown("#### ✅ Final answer (what the user sees)")
        st.markdown(guarded["content"])

    with st.expander("📜 Live proxy log (same as the terminal)", expanded=False):
        st.caption(
            "Exactly what the proxy prints to its terminal: user prompt → raw model "
            "output → the middleware's refine prompt → the regenerated answer."
        )
        st.code(
            _build_proxy_log(prompt, history, guarded, model, policy, upstream),
            language="text",
        )

    with st.expander("Raw guard metadata (JSON)"):
        st.json({k: v for k, v in g.items() if k != "history"})
