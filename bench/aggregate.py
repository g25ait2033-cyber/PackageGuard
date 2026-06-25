"""Aggregate elicitation result JSON(s) into final CSV tables for the writeup.

Outputs two CSVs next to the source (or to --outdir):
  * <stem>_hallucinations.csv — one row per unique hallucinated package name,
    with hit count, how many distinct models produced it, the trust score, and
    an example prompt index.
  * <stem>_by_model.csv — per-model run/package/hallucination summary.

Usage:
  python bench/aggregate.py bench/results/elicitation_..._reextracted.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def aggregate(src: Path, outdir: Path | None = None) -> tuple[Path, Path]:
    out = json.loads(src.read_text(encoding="utf-8"))
    runs = out["runs"]
    outdir = outdir or src.parent

    # --- per hallucinated name ---
    count: dict[str, int] = defaultdict(int)
    models_for: dict[str, set[str]] = defaultdict(set)
    score_for: dict[str, float] = {}
    example_prompt: dict[str, int] = {}
    for r in runs:
        verdict_by_name = {v["package"]: v for v in r.get("package_verdicts", [])}
        for name in r.get("hallucinated", []):
            count[name] += 1
            models_for[name].add(r["model"])
            example_prompt.setdefault(name, r["prompt_idx"])
            if name in verdict_by_name:
                score_for[name] = verdict_by_name[name]["score"]

    hall_path = outdir / f"{src.stem}_hallucinations.csv"
    with hall_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["package", "hits", "n_models", "models", "trust_score", "example_prompt_idx"])
        for name in sorted(count, key=lambda n: (-count[n], n)):
            w.writerow([
                name,
                count[name],
                len(models_for[name]),
                ";".join(sorted(models_for[name])),
                score_for.get(name, ""),
                example_prompt.get(name, ""),
            ])

    # --- per model ---
    by_model: dict[str, dict[str, int]] = {}
    uniq_by_model: dict[str, set[str]] = defaultdict(set)
    for r in runs:
        m = r["model"]
        d = by_model.setdefault(m, {"runs": 0, "with_hall": 0, "hall_count": 0, "pkg_count": 0})
        d["runs"] += 1
        d["pkg_count"] += len(r.get("extracted", []))
        if r.get("hallucinated"):
            d["with_hall"] += 1
            d["hall_count"] += len(r["hallucinated"])
            uniq_by_model[m].update(r["hallucinated"])

    model_path = outdir / f"{src.stem}_by_model.csv"
    with model_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "runs", "packages", "runs_with_hall", "hall_pct", "hall_total", "uniq_hall"])
        for m, d in by_model.items():
            pct = round(100 * d["with_hall"] / d["runs"], 1) if d["runs"] else 0
            w.writerow([m, d["runs"], d["pkg_count"], d["with_hall"], pct, d["hall_count"], len(uniq_by_model[m])])

    print(f"Unique hallucinated names: {len(count)}")
    print(f"Total runs: {len(runs)}")
    print(f"Wrote {hall_path}")
    print(f"Wrote {model_path}")
    return hall_path, model_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate elicitation results into CSVs")
    ap.add_argument("src", help="path to a result JSON (use the *_reextracted.json)")
    ap.add_argument("--outdir", default=None, help="directory to write CSVs (default: alongside src)")
    args = ap.parse_args(argv)
    aggregate(Path(args.src), Path(args.outdir) if args.outdir else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
