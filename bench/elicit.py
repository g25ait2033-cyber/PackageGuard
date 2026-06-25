"""Elicit real package hallucinations from local LLMs.

Cycles:  models  x  prompts  x  N samples per prompt
For each completion, extract package names and look them up in the trust engine.
A package with verdict==BLOCK is a candidate hallucination.

Output: bench/results/elicitation_<UTC ts>.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a plain script (`python bench/elicit.py`) by putting the
# project root on sys.path before importing first-party packages.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packageguard import extract, llm_client
from trust_engine.score import evaluate

DEFAULT_PROMPTS = Path(__file__).parent / "niche_prompts.json"
RESULTS_DIR = Path(__file__).parent / "results"
TRANSCRIPTS_DIR = RESULTS_DIR / "transcripts"


def _safe_slug(text: str, n: int = 40) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in text.lower())
    while "--" in keep:
        keep = keep.replace("--", "-")
    return keep.strip("-")[:n] or "prompt"


def _write_transcripts(out: dict, run_id: str) -> Path:
    """Dump each completion as a readable Markdown file so the generated code is easy to inspect."""
    folder = TRANSCRIPTS_DIR / run_id
    folder.mkdir(parents=True, exist_ok=True)
    for n, r in enumerate(out["runs"]):
        model_slug = _safe_slug(r["model"].split("/")[-1], 24)
        fname = f"{n:03d}_{model_slug}_p{r['prompt_idx']:02d}s{r['sample']}.md"
        verdict_lines = []
        for v in r["package_verdicts"]:
            mark = "BLOCK" if v["verdict"] == "BLOCK" else v["verdict"]
            verdict_lines.append(f"- `{v['package']}` -> **{mark}** (score {v['score']})")
        hall = ", ".join(f"`{h}`" for h in r["hallucinated"]) or "_none_"
        body = (
            f"# {r['model']}\n\n"
            f"**Prompt {r['prompt_idx']} (sample {r['sample']})**\n\n"
            f"> {r['prompt']}\n\n"
            f"**Hallucinated (BLOCK):** {hall}\n\n"
            f"**All extracted packages:**\n\n"
            + ("\n".join(verdict_lines) or "_none_")
            + "\n\n---\n\n## Raw LLM output\n\n"
            + r["completion"]
            + "\n"
        )
        (folder / fname).write_text(body, encoding="utf-8")
    return folder


def run(
    models: list[str],
    prompts: list[str],
    *,
    host: str = "lmstudio",
    n_samples: int = 2,
    temperature: float = 1.0,
    max_tokens: int = 600,
    out_path: Path | None = None,
) -> dict:
    if host == "ollama":
        client = llm_client.make_ollama_client()
    else:
        client = llm_client.make_client()
    available = set(llm_client.list_models(client))
    missing = [m for m in models if m not in available]
    if missing:
        print(f"[warn] models not loaded on {host}: {missing}", file=sys.stderr)
        print(f"       available: {sorted(available)[:8]}{'...' if len(available)>8 else ''}", file=sys.stderr)

    runs: list[dict] = []
    pkg_cache: dict[str, dict] = {}  # avoid re-evaluating same package across rows
    total = len(models) * len(prompts) * n_samples
    done = 0
    t0 = time.time()

    for model in models:
        if model not in available:
            continue
        for i, prompt in enumerate(prompts):
            for s in range(n_samples):
                done += 1
                t_call = time.time()
                try:
                    completions = llm_client.chat(
                        client, model=model,
                        messages=[
                            {"role": "system", "content": "You are a helpful Python coding assistant. Always show pip install commands and runnable code."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=temperature, max_tokens=max_tokens, n=1,
                    )
                except Exception as e:
                    print(f"[{done}/{total}] {model} err: {e}", file=sys.stderr)
                    continue
                completion = completions[0] if completions else ""
                names = sorted(extract.extract(completion))

                pkg_reports = []
                for name in names:
                    if name not in pkg_cache:
                        rep = evaluate(name, "pypi")
                        pkg_cache[name] = {
                            "package": rep.package,
                            "score": rep.score,
                            "verdict": rep.verdict,
                        }
                    pkg_reports.append(pkg_cache[name])

                hallucinated = [p["package"] for p in pkg_reports if p["verdict"] == "BLOCK"]
                runs.append({
                    "model": model,
                    "prompt_idx": i,
                    "prompt": prompt,
                    "sample": s,
                    "elapsed_s": round(time.time() - t_call, 2),
                    "extracted": names,
                    "package_verdicts": pkg_reports,
                    "hallucinated": hallucinated,
                    "completion": completion,
                })
                tag = "HALL" if hallucinated else "ok  "
                print(f"[{done}/{total}] {tag} {model[:30]:30} p{i:02d}s{s} -> {hallucinated or names[:3]}")

    out = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "params": {"n_samples": n_samples, "temperature": temperature, "max_tokens": max_tokens},
        "models_used": [m for m in models if m in available],
        "prompt_count": len(prompts),
        "total_runs": len(runs),
        "elapsed_s": round(time.time() - t0, 1),
        "runs": runs,
    }
    out_path = out_path or (RESULTS_DIR / f"elicitation_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    transcripts = _write_transcripts(out, out_path.stem)
    _print_summary(out)
    print(f"\nWrote {out_path}")
    print(f"Readable transcripts: {transcripts}")
    return out


def reextract(src: Path, out_path: Path | None = None) -> dict:
    """Re-run extraction + trust verdicts on the saved completions of an existing
    result JSON. No LLM calls — uses the stored `completion` text, so extractor or
    scoring improvements can be applied to past runs without re-generating code."""
    out = json.loads(src.read_text(encoding="utf-8"))
    pkg_cache: dict[str, dict] = {}
    for r in out["runs"]:
        names = sorted(extract.extract(r.get("completion", "")))
        pkg_reports = []
        for name in names:
            if name not in pkg_cache:
                rep = evaluate(name, "pypi")
                pkg_cache[name] = {
                    "package": rep.package,
                    "score": rep.score,
                    "verdict": rep.verdict,
                }
            pkg_reports.append(pkg_cache[name])
        r["extracted"] = names
        r["package_verdicts"] = pkg_reports
        r["hallucinated"] = [p["package"] for p in pkg_reports if p["verdict"] == "BLOCK"]
    out["total_runs"] = len(out["runs"])
    out["reextracted_from"] = src.name
    out_path = out_path or src.with_name(src.stem + "_reextracted.json")
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _write_transcripts(out, out_path.stem)
    _print_summary(out)
    print(f"\nWrote {out_path}")
    return out


def _print_summary(out: dict) -> None:
    by_model: dict[str, dict[str, int]] = {}
    for r in out["runs"]:
        m = r["model"]
        d = by_model.setdefault(m, {"runs": 0, "with_hall": 0, "hall_count": 0, "pkg_count": 0})
        d["runs"] += 1
        d["pkg_count"] += len(r["extracted"])
        if r["hallucinated"]:
            d["with_hall"] += 1
            d["hall_count"] += len(r["hallucinated"])

    print("\n=== Summary ===")
    print(f"{'model':40} {'runs':>5} {'pkgs':>5} {'hall%':>6} {'hall':>5}")
    for m, d in by_model.items():
        pct = 100 * d["with_hall"] / d["runs"] if d["runs"] else 0
        print(f"{m[:40]:40} {d['runs']:>5} {d['pkg_count']:>5} {pct:>5.1f}% {d['hall_count']:>5}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Elicit package hallucinations from local LLMs")
    ap.add_argument("--models", nargs="+", help="model IDs as listed by /v1/models")
    ap.add_argument("--host", choices=["lmstudio", "ollama"], default="lmstudio", help="which local backend to call")
    ap.add_argument("--prompts", default=str(DEFAULT_PROMPTS), help="path to JSON list of prompts")
    ap.add_argument("--samples", type=int, default=2, help="completions per prompt")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--limit", type=int, default=None, help="use only the first N prompts (quick smoke run)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--from-json", default=None, help="regenerate readable transcripts from an existing result JSON (no LLM calls)")
    ap.add_argument("--reextract", default=None, help="re-run extraction + trust verdicts on an existing result JSON's saved completions (no LLM calls)")
    args = ap.parse_args(argv)

    if args.reextract:
        src = Path(args.reextract)
        out_path = Path(args.out) if args.out else None
        reextract(src, out_path)
        return 0

    if args.from_json:
        src = Path(args.from_json)
        out = json.loads(src.read_text(encoding="utf-8"))
        folder = _write_transcripts(out, src.stem)
        print(f"Wrote {len(out['runs'])} transcripts to {folder}")
        return 0

    if not args.models:
        ap.error("--models is required unless --from-json is given")
    prompts = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
    if args.limit:
        prompts = prompts[: args.limit]
    out_path = Path(args.out) if args.out else None
    run(
        models=args.models, prompts=prompts,
        host=args.host,
        n_samples=args.samples, temperature=args.temperature,
        max_tokens=args.max_tokens, out_path=out_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
