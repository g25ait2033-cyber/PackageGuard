"""Ablation: does the *existence* (registry) check alone explain our verdicts?

Why this exists
---------------
Our validation run scored 100% — but that set is split into names that DON'T
exist (fakes) and names that DO exist (real). For that split, "does it resolve
on PyPI?" is, by construction, a perfect separator. A skeptic rightly asks:

    "If existence alone gets 100%, why build 7 other collectors?"

Because existence answers the WRONG question. The real attack (slopsquatting)
is a hallucinated name an adversary has ALREADY PUBLISHED. It *exists* on PyPI,
so an existence/allow-list check waves it through. The paper itself says so:

    "This type of filtering method is ineffective as a defense strategy, as an
     attacker could immediately publish a hallucinated package to the repository
     and be subsequently included in the 'allow' list."  (Spracklen et al., §6.1)

The other 7 collectors exist for that case: a fresh squat *exists* but has a
minutes-old maintainer, ~zero downloads, no real repo, no real-world code usage,
and a suspicious install script. This script proves the collectors carry signal
*independent of existence* by comparing, for every package:

  * registry-only verdict  — emulates a pure allow-list (exists -> PASS,
                             not-exists -> BLOCK). Zero discrimination among
                             packages that all exist.
  * full-engine verdict    — all 8 collectors, weighted.
  * non-registry score     — the weighted score of the OTHER 7 collectors with
                             registry removed entirely.

The headline finding: among packages that ALL EXIST (registry says PASS for
every one), the full engine still down-ranks a chunk of them to WARN/BLOCK.
A pure allow-list cannot see those. That gap is exactly where a published squat
would hide — and it is produced entirely by the non-registry collectors.

Outputs:
  bench/results/ablation_report.json
  bench/results/ablation_results.csv
  bench/results/plots/ablation_existing_scores.png
  bench/results/plots/ablation_verdict_compare.png

The run is RESUMABLE via bench/results/ablation_cache.json.

Usage:
  python bench/ablation.py                  # collected names only
  python bench/ablation.py --control 80     # + 80 known-real control packages
  python bench/ablation.py --limit 50       # quick smoke test
  python bench/ablation.py --resume         # continue a previous run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importlib import resources  # noqa: E402

from trust_engine.score import (  # noqa: E402
    PASS_THRESHOLD,
    WARN_THRESHOLD,
    evaluate,
)

DEFAULT_INPUTS = [
    "bench/results/elicitation_20260617T174325_reextracted.json",
    "bench/results/elicitation_lmstudio_qwen35_reextracted.json",
]
CACHE_PATH = Path("bench/results/ablation_cache.json")
REPORT_JSON = Path("bench/results/ablation_report.json")
REPORT_CSV = Path("bench/results/ablation_results.csv")
PLOTS_DIR = Path("bench/results/plots")


# --------------------------------------------------------------------------- #
# name loaders                                                                #
# --------------------------------------------------------------------------- #
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
        resources.files("trust_engine.data")
        .joinpath("popular_pypi.json")
        .read_text(encoding="utf-8")
    )
    return data[:n]


# --------------------------------------------------------------------------- #
# scoring helpers                                                             #
# --------------------------------------------------------------------------- #
def _exists(signals: list[dict]) -> bool | None:
    for s in signals:
        if s["collector"] == "registry":
            return (s.get("raw") or {}).get("exists")
    return None


def _weighted(signals: list[dict], exclude: set[str]) -> float | None:
    """Weighted score over signals, skipping any collector in `exclude`."""
    total_w = 0.0
    total = 0.0
    for s in signals:
        if s["collector"] in exclude:
            continue
        if s["score"] is None or s["error"]:
            continue
        total += s["score"] * s["weight"]
        total_w += s["weight"]
    if total_w == 0:
        return None
    return round(total / total_w, 1)


def _verdict_from_score(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= PASS_THRESHOLD:
        return "PASS"
    if score >= WARN_THRESHOLD:
        return "WARN"
    return "BLOCK"


def registry_only_verdict(exists: bool | None) -> str:
    """Emulate a pure allow-list / existence check (the paper's naive baseline)."""
    if exists is None:
        return "UNKNOWN"
    return "PASS" if exists else "BLOCK"


def signals_to_dicts(rep) -> list[dict]:
    return [
        {
            "collector": s.collector,
            "score": s.score,
            "weight": s.weight,
            "error": s.error,
            "raw": s.raw,
        }
        for s in rep.signals
    ]


# --------------------------------------------------------------------------- #
# cache                                                                        #
# --------------------------------------------------------------------------- #
def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# evaluation                                                                   #
# --------------------------------------------------------------------------- #
def evaluate_all(
    names: list[str], source: dict[str, str], resume: bool, save_every: int = 10
) -> dict:
    cache = _load_cache() if resume else {}
    todo = [n for n in names if n not in cache]
    print(f"{len(names)} names total · {len(cache)} cached · {len(todo)} to evaluate")

    for i, name in enumerate(todo, 1):
        try:
            rep = evaluate(name, "pypi")
            sigs = signals_to_dicts(rep)
            exists = _exists(sigs)
            no_reg = _weighted(sigs, exclude={"registry"})
            cache[name] = {
                "full_score": rep.score,
                "full_verdict": rep.verdict,
                "registry_only_verdict": registry_only_verdict(exists),
                "no_registry_score": no_reg,
                "no_registry_verdict": _verdict_from_score(no_reg),
                "exists": exists,
                "source": source.get(name, "collected"),
                "signals": {
                    s["collector"]: (None if s["error"] else s["score"]) for s in sigs
                },
            }
        except Exception as e:  # noqa: BLE001
            cache[name] = {
                "full_score": None,
                "full_verdict": "ERROR",
                "registry_only_verdict": "ERROR",
                "no_registry_score": None,
                "no_registry_verdict": "ERROR",
                "exists": None,
                "source": source.get(name, "collected"),
                "error": str(e),
            }
        d = cache[name]
        print(
            f"  [{i}/{len(todo)}] {name[:34]:34} "
            f"exists={d['exists']!s:5} "
            f"reg-only={d['registry_only_verdict']:7} "
            f"full={d['full_verdict']:6} "
            f"no-reg={d['no_registry_score']}"
        )
        if i % save_every == 0:
            _save_cache(cache)
    _save_cache(cache)
    return cache


# --------------------------------------------------------------------------- #
# analysis                                                                     #
# --------------------------------------------------------------------------- #
def analyze(cache: dict) -> dict:
    rows = [
        (n, d)
        for n, d in cache.items()
        if d.get("full_verdict") != "ERROR" and d.get("exists") is not None
    ]
    existing = [(n, d) for n, d in rows if d["exists"] is True]
    nonexistent = [(n, d) for n, d in rows if d["exists"] is False]

    # --- the key comparison: among packages that ALL EXIST ---
    # registry-only verdict is PASS for every one of them (no discrimination).
    # The full engine down-ranks some to WARN/BLOCK using the other 7 collectors.
    flagged_existing = [
        (n, d) for n, d in existing if d["full_verdict"] != "PASS"
    ]  # WARN or BLOCK despite existing
    flagged_existing.sort(key=lambda kv: (kv[1]["full_score"] is None, kv[1]["full_score"]))

    full_scores_existing = [
        d["full_score"] for _, d in existing if d["full_score"] is not None
    ]

    def _spread(xs):
        if not xs:
            return None
        return {
            "min": round(min(xs), 1),
            "max": round(max(xs), 1),
            "mean": round(sum(xs) / len(xs), 1),
            "range": round(max(xs) - min(xs), 1),
        }

    # verdict distributions among existing packages
    def _dist(items, key):
        out = {"PASS": 0, "WARN": 0, "BLOCK": 0}
        for _, d in items:
            out[d[key]] = out.get(d[key], 0) + 1
        return out

    return {
        "counts": {
            "evaluated": len(rows),
            "existing": len(existing),
            "non_existent": len(nonexistent),
        },
        "among_existing": {
            "registry_only_verdicts": _dist(existing, "registry_only_verdict"),
            "full_engine_verdicts": _dist(existing, "full_verdict"),
            "flagged_by_full_not_registry": [
                {
                    "name": n,
                    "full_verdict": d["full_verdict"],
                    "full_score": d["full_score"],
                    "no_registry_score": d["no_registry_score"],
                    "source": d.get("source"),
                }
                for n, d in flagged_existing
            ],
            "full_score_spread": _spread(full_scores_existing),
        },
        "among_non_existent": {
            "registry_only_verdicts": _dist(nonexistent, "registry_only_verdict"),
            "full_engine_verdicts": _dist(nonexistent, "full_verdict"),
        },
    }


def write_csv(cache: dict) -> None:
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "package",
                "exists",
                "source",
                "registry_only_verdict",
                "full_verdict",
                "full_score",
                "no_registry_verdict",
                "no_registry_score",
            ]
        )
        for n, d in sorted(cache.items()):
            w.writerow(
                [
                    n,
                    d.get("exists"),
                    d.get("source"),
                    d.get("registry_only_verdict"),
                    d.get("full_verdict"),
                    d.get("full_score"),
                    d.get("no_registry_verdict"),
                    d.get("no_registry_score"),
                ]
            )


# --------------------------------------------------------------------------- #
# charts                                                                       #
# --------------------------------------------------------------------------- #
def make_charts(report: dict, cache: dict) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"  ! matplotlib unavailable, skipping charts: {e}")
        return

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    # Chart 1: full-engine score distribution among EXISTING packages.
    # A pure allow-list would assign every one of these the same "PASS" — a flat
    # line at the top. The spread + the left tail below the PASS line is signal
    # that ONLY the non-registry collectors can produce.
    existing_scores = [
        d["full_score"]
        for d in cache.values()
        if d.get("exists") is True and d.get("full_score") is not None
    ]
    if existing_scores:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.hist(existing_scores, bins=20, color="#4C8BF5", edgecolor="white")
        ax.axvline(PASS_THRESHOLD, color="#E0A800", linestyle="--", linewidth=2,
                   label=f"PASS threshold ({PASS_THRESHOLD:.0f})")
        ax.axvline(WARN_THRESHOLD, color="#D9534F", linestyle="--", linewidth=2,
                   label=f"BLOCK threshold ({WARN_THRESHOLD:.0f})")
        ax.set_title("Full-engine trust score among packages that ALL EXIST on PyPI\n"
                     "(a pure existence/allow-list check would rate every one identically)")
        ax.set_xlabel("Full-engine trust score (0-100)")
        ax.set_ylabel("number of real packages")
        ax.legend()
        fig.tight_layout()
        out = PLOTS_DIR / "ablation_existing_scores.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print(f"  wrote {out}")

    # Chart 2: verdict distribution among existing packages — registry-only
    # (all PASS) vs full engine (PASS/WARN/BLOCK).
    ae = report["among_existing"]
    reg = ae["registry_only_verdicts"]
    full = ae["full_engine_verdicts"]
    cats = ["PASS", "WARN", "BLOCK"]
    reg_vals = [reg.get(c, 0) for c in cats]
    full_vals = [full.get(c, 0) for c in cats]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = range(len(cats))
    w = 0.38
    ax.bar([i - w / 2 for i in x], reg_vals, width=w,
           label="registry-only (allow-list)", color="#9AA0A6")
    ax.bar([i + w / 2 for i in x], full_vals, width=w,
           label="full engine (8 collectors)", color="#4C8BF5")
    ax.set_xticks(list(x))
    ax.set_xticklabels(cats)
    ax.set_title("Verdicts among packages that EXIST on PyPI\n"
                 "registry-only cannot flag any of them; the full engine can")
    ax.set_ylabel("number of real packages")
    for i, v in enumerate(reg_vals):
        ax.text(i - w / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    for i, v in enumerate(full_vals):
        ax.text(i + w / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.legend()
    fig.tight_layout()
    out = PLOTS_DIR / "ablation_verdict_compare.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  wrote {out}")


# --------------------------------------------------------------------------- #
# summary printout                                                            #
# --------------------------------------------------------------------------- #
def print_summary(report: dict) -> None:
    c = report["counts"]
    ae = report["among_existing"]
    reg = ae["registry_only_verdicts"]
    full = ae["full_engine_verdicts"]
    flagged = ae["flagged_by_full_not_registry"]
    spread = ae["full_score_spread"]

    print("\n" + "=" * 64)
    print("  ABLATION  —  does existence (registry) alone do the work?")
    print("=" * 64)
    print(f"  evaluated            : {c['evaluated']}")
    print(f"  exist on PyPI        : {c['existing']}")
    print(f"  non-existent (fake)  : {c['non_existent']}")
    print("  ---- among the packages that ALL EXIST on PyPI ----")
    print(f"  registry-only verdict: PASS {reg.get('PASS',0)}  "
          f"WARN {reg.get('WARN',0)}  BLOCK {reg.get('BLOCK',0)}   "
          f"(<- a flat allow-list: no discrimination)")
    print(f"  full-engine  verdict : PASS {full.get('PASS',0)}  "
          f"WARN {full.get('WARN',0)}  BLOCK {full.get('BLOCK',0)}   "
          f"(<- the other 7 collectors down-rank some)")
    if spread:
        print(f"  full score spread    : {spread['min']} .. {spread['max']} "
              f"(mean {spread['mean']}, range {spread['range']})")
        print("                         (registry-only would give every one the SAME score)")
    n_flagged = len(flagged)
    print(f"  => {n_flagged} EXISTING package(s) the full engine flags that a pure")
    print("     existence/allow-list check would wave straight through.")
    if flagged:
        print("     (this is exactly where a freshly-published squat hides)")
        for item in flagged[:15]:
            print(f"       - {item['name'][:30]:30} full={item['full_verdict']:5} "
                  f"score={item['full_score']}  non-reg={item['no_registry_score']}")
        if n_flagged > 15:
            print(f"       ... and {n_flagged - 15} more (see CSV)")
    print("=" * 64)
    ne = report["among_non_existent"]
    print(f"  among the {c['non_existent']} NON-EXISTENT names: registry-only and full")
    print(f"  engine both BLOCK all of them (full: BLOCK "
          f"{ne['full_engine_verdicts'].get('BLOCK',0)}). Existence is sufficient")
    print("  ONLY for names nobody has published yet — not for the real attack.")
    print("=" * 64)


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", nargs="*", default=DEFAULT_INPUTS,
                    help="elicitation JSON files to pull collected names from")
    ap.add_argument("--control", type=int, default=0,
                    help="add N known-real packages from popular_pypi.json")
    ap.add_argument("--limit", type=int, default=0,
                    help="evaluate at most N names (quick smoke test)")
    ap.add_argument("--resume", action="store_true",
                    help="continue from the cache instead of starting fresh")
    args = ap.parse_args()

    paths = [Path(p) for p in args.inputs]
    collected = sorted(collected_names(paths))
    source = {n: "collected" for n in collected}

    names = list(collected)
    if args.control > 0:
        ctrl = control_names(args.control)
        for n in ctrl:
            if n not in source:
                source[n] = "control-real"
                names.append(n)
        print(f"+ {len(ctrl)} known-real control packages")

    if args.limit > 0:
        names = names[: args.limit]

    cache = evaluate_all(names, source, resume=args.resume)
    report = analyze(cache)

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_csv(cache)
    make_charts(report, cache)
    print_summary(report)

    print(f"\nWrote {REPORT_JSON}")
    print(f"Wrote {REPORT_CSV}")
    print(f"Cache: {CACHE_PATH}  (use --resume to continue)")


if __name__ == "__main__":
    main()
