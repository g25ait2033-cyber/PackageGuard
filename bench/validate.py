"""Validate the trust engine's accuracy against INDEPENDENT ground truth.

The honest question: of every package name we collected, did the engine BLOCK the
ones that genuinely don't exist on PyPI, and did it WRONGLY block any that do?

Crucially, ground truth here is NOT the engine's own verdict (that would be
circular). Ground truth = live PyPI existence:
  * exists == False  -> the name is genuinely non-existent (a true hallucination).
                        The engine SHOULD block it.  Missing it = false negative.
  * exists == True   -> the package is real on PyPI.
                        If the engine blocks it, that's a FALSE POSITIVE we list
                        by name for manual review.

We test two populations:
  1. Collected names   — every package extracted during elicitation (mix of fake
                         + real, since models also name real packages).
  2. Known-real control — popular_pypi.json (all definitely real & legitimate);
                         any BLOCK here is a clear false positive.

The run is RESUMABLE: results stream to a JSON cache so a long run can be stopped
and continued, and reviewed afterwards.

Usage:
  python bench/validate.py                       # collected names only
  python bench/validate.py --control 80          # + 80 known-real packages
  python bench/validate.py --limit 50            # quick smoke test
  python bench/validate.py --resume              # continue a previous run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importlib import resources  # noqa: E402

from trust_engine.score import evaluate  # noqa: E402

DEFAULT_INPUTS = [
    "bench/results/elicitation_20260617T174325_reextracted.json",
    "bench/results/elicitation_lmstudio_qwen35_reextracted.json",
]
CACHE_PATH = Path("bench/results/validation_cache.json")
REPORT_JSON = Path("bench/results/validation_report.json")
REPORT_CSV = Path("bench/results/validation_results.csv")


def collected_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for p in paths:
        if not p.exists():
            print(f"  ! skipping missing input: {p}")
            continue
        for r in json.loads(p.read_text(encoding="utf-8")).get("runs", []):
            names.update(r.get("extracted", []))
    return names


def control_names(n: int) -> list[str]:
    data = json.loads(
        resources.files("trust_engine.data").joinpath("popular_pypi.json").read_text(encoding="utf-8")
    )
    return data[:n]


def _exists_from_report(rep) -> bool | None:
    for s in rep.signals:
        if s.collector == "registry" and s.raw:
            return s.raw.get("exists")
    return None


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def evaluate_all(names: list[str], source: dict[str, str], resume: bool, save_every: int = 10) -> dict:
    cache = _load_cache() if resume else {}
    todo = [n for n in names if n not in cache]
    print(f"{len(names)} names total · {len(cache)} cached · {len(todo)} to evaluate")

    for i, name in enumerate(todo, 1):
        try:
            rep = evaluate(name, "pypi")
            cache[name] = {
                "score": rep.score,
                "verdict": rep.verdict,
                "exists": _exists_from_report(rep),
                "source": source.get(name, "collected"),
            }
        except Exception as e:  # noqa: BLE001
            cache[name] = {"score": None, "verdict": "ERROR", "exists": None,
                           "source": source.get(name, "collected"), "error": str(e)}
        mark = cache[name]["verdict"]
        ex = cache[name]["exists"]
        print(f"  [{i}/{len(todo)}] {name[:38]:38} exists={ex!s:5} -> {mark}")
        if i % save_every == 0:
            _save_cache(cache)
    _save_cache(cache)
    return cache


def analyze(cache: dict) -> dict:
    """Compute accuracy with PyPI existence as ground truth."""
    # ground-truth positives = non-existent (true hallucinations)
    # ground-truth negatives = exists on PyPI (real)
    rows = [(n, d) for n, d in cache.items() if d.get("verdict") != "ERROR" and d.get("exists") is not None]

    non_existent = [(n, d) for n, d in rows if d["exists"] is False]
    real = [(n, d) for n, d in rows if d["exists"] is True]

    blocked = lambda d: d["verdict"] == "BLOCK"

    tp = [n for n, d in non_existent if blocked(d)]         # fake & blocked (correct)
    fn = [n for n, d in non_existent if not blocked(d)]     # fake & allowed (MISS)
    fp = [n for n, d in real if blocked(d)]                 # real & blocked (FALSE ALARM)
    tn = [n for n, d in real if not blocked(d)]             # real & allowed (correct)

    # WARN treated as "not blocked" above; track separately for nuance
    warn_real = [n for n, d in real if d["verdict"] == "WARN"]
    warn_fake = [n for n, d in non_existent if d["verdict"] == "WARN"]

    n_pos = len(non_existent)
    n_neg = len(real)
    recall = len(tp) / n_pos if n_pos else None
    far = len(fp) / n_neg if n_neg else None
    precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) else None
    accuracy = (len(tp) + len(tn)) / (n_pos + n_neg) if (n_pos + n_neg) else None

    return {
        "counts": {
            "evaluated": len(rows),
            "non_existent_truth": n_pos,
            "real_truth": n_neg,
            "errors": sum(1 for d in cache.values() if d.get("verdict") == "ERROR"),
        },
        "confusion": {
            "true_positive_fake_blocked": len(tp),
            "false_negative_fake_allowed": len(fn),
            "false_positive_real_blocked": len(fp),
            "true_negative_real_allowed": len(tn),
        },
        "metrics": {
            "recall_on_fakes": recall,
            "false_alarm_rate_on_real": far,
            "precision": precision,
            "accuracy": accuracy,
        },
        "warn_real": warn_real,
        "warn_fake": warn_fake,
        "false_negatives": sorted(fn),       # hallucinations we MISSED — investigate
        "false_positives": sorted(fp),       # real packages we wrongly BLOCKED — investigate
    }


def write_csv(cache: dict) -> None:
    import csv
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["package", "exists_on_pypi", "verdict", "score", "source",
                    "correct"])
        for name, d in sorted(cache.items()):
            ex = d.get("exists")
            verdict = d.get("verdict")
            # "correct" = blocked iff non-existent (warn counts as not-blocked)
            if ex is None or verdict == "ERROR":
                correct = ""
            elif ex is False:
                correct = "yes" if verdict == "BLOCK" else "no"   # should block
            else:
                correct = "no" if verdict == "BLOCK" else "yes"   # should allow
            w.writerow([name, ex, verdict, d.get("score"), d.get("source", ""), correct])


def _pct(x):
    return "n/a" if x is None else f"{x*100:.1f}%"


def print_summary(report: dict) -> None:
    c = report["counts"]; cm = report["confusion"]; m = report["metrics"]
    print("\n" + "=" * 60)
    print("  VALIDATION SUMMARY  (ground truth = live PyPI existence)")
    print("=" * 60)
    print(f"  evaluated           : {c['evaluated']}  (errors: {c['errors']})")
    print(f"  non-existent (fake) : {c['non_existent_truth']}")
    print(f"  real on PyPI        : {c['real_truth']}")
    print("  ---- confusion (predict BLOCK = 'caught') ----")
    print(f"  fake & blocked  (TP): {cm['true_positive_fake_blocked']}")
    print(f"  fake & allowed  (FN): {cm['false_negative_fake_allowed']}   <- missed hallucinations")
    print(f"  real & blocked  (FP): {cm['false_positive_real_blocked']}   <- wrongly flagged real pkgs")
    print(f"  real & allowed  (TN): {cm['true_negative_real_allowed']}")
    print("  ---- metrics ----")
    print(f"  recall on fakes     : {_pct(m['recall_on_fakes'])}   (did we catch the hallucinations)")
    print(f"  false-alarm on real : {_pct(m['false_alarm_rate_on_real'])}   (real pkgs wrongly blocked)")
    print(f"  precision           : {_pct(m['precision'])}")
    print(f"  accuracy            : {_pct(m['accuracy'])}")
    if report["false_negatives"]:
        print(f"\n  MISSED hallucinations ({len(report['false_negatives'])}): {report['false_negatives'][:20]}")
    if report["false_positives"]:
        print(f"\n  FALSE positives ({len(report['false_positives'])}): {report['false_positives'][:20]}")
    print("=" * 60)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate trust-engine accuracy vs PyPI ground truth")
    ap.add_argument("--inputs", nargs="*", default=DEFAULT_INPUTS)
    ap.add_argument("--control", type=int, default=0, help="add N known-real packages as control")
    ap.add_argument("--limit", type=int, default=0, help="cap collected names (smoke test)")
    ap.add_argument("--resume", action="store_true", help="continue from validation_cache.json")
    args = ap.parse_args(argv)

    collected = sorted(collected_names([Path(p) for p in args.inputs]))
    if args.limit:
        collected = collected[:args.limit]
    source = {n: "collected" for n in collected}

    names = list(collected)
    if args.control:
        ctrl = control_names(args.control)
        for n in ctrl:
            source.setdefault(n, "control")
            if n not in source or source[n] == "control":
                if n not in names:
                    names.append(n)
        # mark control explicitly
        for n in ctrl:
            source[n] = "control" if n not in collected else "both"

    t0 = time.time()
    cache = evaluate_all(names, source, resume=args.resume)
    report = analyze(cache)
    report["elapsed_s"] = round(time.time() - t0, 1)

    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_csv(cache)
    print_summary(report)
    print(f"\nWrote {REPORT_JSON}")
    print(f"Wrote {REPORT_CSV}")
    print(f"Cache:  {CACHE_PATH}  (use --resume to continue)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
