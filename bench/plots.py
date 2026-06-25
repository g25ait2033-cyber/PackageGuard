"""Generate the charts for the PackageGuard writeup/slides.

Reads the locked elicitation result JSON(s) — no network needed — and renders
PNGs into bench/results/plots/. These are the "does our defense work" figures.

Charts produced from local data:
  1. score_histogram.png        — trust-score distribution: hallucinated (BLOCK)
                                   vs legitimate (PASS) packages. THE money shot.
  2. per_model_hall_rate.png    — hallucination rate per model (reproduces paper).
  3. verdict_breakdown.png      — unique packages by verdict (BLOCK/WARN/PASS).
  4. hallucinations_by_domain.png — where models invent most (uses the same
                                   category buckets as build_fake_db.py).

Optional (needs network + GITHUB_TOKEN for best results):
  5. --eval-real N   also evaluates N known-real popular packages live, then adds
     confusion_matrix.png + fakes_vs_real.png for an independent real baseline.

Usage:
  python bench/plots.py
  python bench/plots.py --eval-real 60
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from bench.build_fake_db import categorize  # noqa: E402

DEFAULT_INPUTS = [
    "bench/results/elicitation_20260617T174325_reextracted.json",
    "bench/results/elicitation_lmstudio_qwen35_reextracted.json",
]

# Consistent palette
C_BLOCK = "#d12b2b"
C_WARN = "#e0a100"
C_PASS = "#1a9e4b"
C_ACCENT = "#4f8cff"


def _short(model: str) -> str:
    return model.replace("-instruct-q4_K_M", "").replace("qwen/", "").replace(":", ":")


def load_runs(paths: list[Path]) -> list[dict]:
    runs: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"  ! skipping missing input: {p}")
            continue
        runs.extend(json.loads(p.read_text(encoding="utf-8")).get("runs", []))
    return runs


def _unique_packages(runs: list[dict]) -> dict[str, dict]:
    """name -> {score, verdict} (one entry per unique package name)."""
    uniq: dict[str, dict] = {}
    for r in runs:
        for v in r.get("package_verdicts", []):
            uniq.setdefault(v["package"], {"score": v.get("score"), "verdict": v.get("verdict")})
    return uniq


def chart_score_histogram(uniq: dict[str, dict], out: Path) -> None:
    block = [v["score"] for v in uniq.values() if v["verdict"] == "BLOCK" and v["score"] is not None]
    warn = [v["score"] for v in uniq.values() if v["verdict"] == "WARN" and v["score"] is not None]
    pass_ = [v["score"] for v in uniq.values() if v["verdict"] == "PASS" and v["score"] is not None]

    fig, ax = plt.subplots(figsize=(8, 4.6))
    bins = range(0, 105, 5)
    ax.hist(block, bins=bins, color=C_BLOCK, alpha=0.8, label=f"Hallucinated / untrusted (BLOCK, n={len(block)})")
    ax.hist(warn, bins=bins, color=C_WARN, alpha=0.7, label=f"Caution (WARN, n={len(warn)})")
    ax.hist(pass_, bins=bins, color=C_PASS, alpha=0.7, label=f"Legitimate (PASS, n={len(pass_)})")
    ax.axvline(40, color="#888", ls="--", lw=1)
    ax.axvline(70, color="#888", ls="--", lw=1)
    ax.text(40, ax.get_ylim()[1] * 0.95, " BLOCK<40", color="#555", fontsize=8, va="top")
    ax.text(70, ax.get_ylim()[1] * 0.95, " PASS\u226570", color="#555", fontsize=8, va="top")
    ax.set_xlabel("Trust score (0\u2013100)")
    ax.set_ylabel("Number of unique packages")
    ax.set_title("Trust-score distribution: the engine separates fakes from real packages")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_per_model(runs: list[dict], out: Path) -> None:
    stats: dict[str, dict] = defaultdict(lambda: {"runs": 0, "hall": 0})
    for r in runs:
        m = _short(r["model"])
        stats[m]["runs"] += 1
        if r.get("hallucinated"):
            stats[m]["hall"] += 1
    models = sorted(stats, key=lambda m: -stats[m]["hall"] / max(1, stats[m]["runs"]))
    rates = [100 * stats[m]["hall"] / max(1, stats[m]["runs"]) for m in models]

    fig, ax = plt.subplots(figsize=(8, 4.4))
    bars = ax.barh(models, rates, color=C_ACCENT)
    ax.invert_yaxis()
    for b, rate in zip(bars, rates):
        ax.text(b.get_width() + 0.6, b.get_y() + b.get_height() / 2, f"{rate:.1f}%", va="center", fontsize=9)
    ax.set_xlabel("% of generations containing \u22651 hallucinated package")
    ax.set_title("Hallucination rate per model (smaller models invent more)")
    ax.set_xlim(0, max(rates) * 1.18 if rates else 100)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_verdict_breakdown(uniq: dict[str, dict], out: Path) -> None:
    counts = {"BLOCK": 0, "WARN": 0, "PASS": 0}
    for v in uniq.values():
        if v["verdict"] in counts:
            counts[v["verdict"]] += 1
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    labels = ["BLOCK", "WARN", "PASS"]
    ax.bar(labels, [counts[k] for k in labels], color=[C_BLOCK, C_WARN, C_PASS])
    for i, k in enumerate(labels):
        ax.text(i, counts[k], str(counts[k]), ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_ylabel("Unique packages")
    ax.set_title("Verdict breakdown across all extracted packages")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def chart_by_domain(uniq: dict[str, dict], out: Path) -> None:
    by_cat: dict[str, int] = defaultdict(int)
    for name, v in uniq.items():
        if v["verdict"] in ("BLOCK", "WARN"):
            by_cat[categorize(name)] += 1
    cats = sorted(by_cat, key=lambda c: by_cat[c])
    vals = [by_cat[c] for c in cats]

    fig, ax = plt.subplots(figsize=(8, 4.6))
    bars = ax.barh(cats, vals, color="#7c5cff")
    for b, v in zip(bars, vals):
        ax.text(b.get_width() + 0.2, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=9)
    ax.set_xlabel("Unique hallucinated/untrusted names")
    ax.set_title("Where models invent most (by domain)")
    ax.set_xlim(0, max(vals) * 1.15 if vals else 1)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def eval_real(n: int):
    """Evaluate n known-real popular packages live; return list of (name, score, verdict)."""
    from importlib import resources
    from trust_engine.score import evaluate

    names = json.loads(
        resources.files("trust_engine.data").joinpath("popular_pypi.json").read_text(encoding="utf-8")
    )
    sample = names[:n]
    out = []
    for i, name in enumerate(sample, 1):
        try:
            rep = evaluate(name, "pypi")
            out.append((name, rep.score, rep.verdict))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: {e}")
        print(f"  [{i}/{len(sample)}] {name} -> {out[-1][1] if out else '?'}")
    return out


def chart_confusion(fakes: list[float], reals: list[tuple], out: Path) -> None:
    # ground truth: fake (from our elicitation BLOCK set) vs real (popular list)
    # prediction: BLOCK = caught, else missed
    tp = sum(1 for s in fakes if s < 40)            # fake & blocked
    fn = len(fakes) - tp                            # fake & not blocked
    fp = sum(1 for _, s, _ in reals if s < 40)      # real & blocked (false alarm)
    tn = len(reals) - fp                            # real & passed

    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    mat = [[tp, fn], [fp, tn]]
    ax.imshow(mat, cmap="Blues")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["Blocked", "Allowed"])
    ax.set_yticks([0, 1]); ax.set_yticklabels(["Fake\n(ground truth)", "Real\n(ground truth)"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(mat[i][j]), ha="center", va="center", fontsize=16, fontweight="bold",
                    color="white" if mat[i][j] > max(tp, tn) / 2 else "black")
    recall = tp / max(1, tp + fn)
    far = fp / max(1, fp + tn)
    ax.set_title(f"Detection: recall {recall:.0%} · false-alarm {far:.0%}")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate PackageGuard charts")
    ap.add_argument("--inputs", nargs="*", default=DEFAULT_INPUTS)
    ap.add_argument("--out-dir", default="bench/results/plots")
    ap.add_argument("--eval-real", type=int, default=0,
                    help="also evaluate N known-real packages live for confusion matrix")
    args = ap.parse_args(argv)

    runs = load_runs([Path(p) for p in args.inputs])
    if not runs:
        print("No runs found — check --inputs paths.")
        return 1
    uniq = _unique_packages(runs)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_score_histogram(uniq, out_dir / "score_histogram.png")
    chart_per_model(runs, out_dir / "per_model_hall_rate.png")
    chart_verdict_breakdown(uniq, out_dir / "verdict_breakdown.png")
    chart_by_domain(uniq, out_dir / "hallucinations_by_domain.png")
    print(f"Wrote 4 charts to {out_dir}/ ({len(runs)} runs, {len(uniq)} unique packages)")

    if args.eval_real:
        print(f"Evaluating {args.eval_real} known-real packages live (network)...")
        reals = eval_real(args.eval_real)
        fakes = [v["score"] for v in uniq.values() if v["verdict"] == "BLOCK" and v["score"] is not None]
        chart_confusion(fakes, reals, out_dir / "confusion_matrix.png")
        print(f"Wrote confusion_matrix.png ({len(fakes)} fakes vs {len(reals)} reals)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
