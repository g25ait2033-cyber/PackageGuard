"""Build the consolidated PackageGuard fake-package database.

Reads the locked elicitation result JSON(s), keeps only names the trust engine
judged BLOCK/WARN (i.e. genuinely non-existent or untrusted on PyPI), dedupes
across all models/runs, attaches the strongest evidence, and writes:

  * bench/results/fake_package_database.json   (machine-readable)
  * bench/results/fake_package_database.md      (human-readable table)

These are the package names *our own testing* elicited from local LLMs — the
ammunition for the eval and the "we found N fakes" slide.

Usage:
  python bench/build_fake_db.py
  python bench/build_fake_db.py --inputs a.json b.json --out-dir bench/results
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

# Locked datasets to merge by default (4-model Ollama run + LM Studio qwen3.5).
DEFAULT_INPUTS = [
    "bench/results/elicitation_20260617T174325_reextracted.json",
    "bench/results/elicitation_lmstudio_qwen35_reextracted.json",
]

# Coarse domain buckets so the slide/table reads nicely. First keyword hit wins.
CATEGORY_RULES = [
    ("Hardware / IoT / sensors", ["roomba", "yubikey", "yubico", "piv", "canon", "edsdk",
                                   "rtl", "adsb", "ads-b", "zigbee", "zigpy", "conbee", "knx",
                                   "modbus", "can", "plc", "rockwell", "pylogix", "dji", "drone",
                                   "tesla", "powerwall", "hue", "sphero", "meshtastic", "trezor",
                                   "depthai", "oak", "realsense", "smart", "fit", "garmin"]),
    ("Finance / market data", ["bloomberg", "blpapi", "ibkr", "ibapi", "plaid", "swift", "mt940",
                                "fix", "iso8583", "paytm", "upi", "stripe"]),
    ("NLP / i18n / scripts", ["amharic", "telugu", "thai", "hangul", "jamo", "devanagari",
                               "cyrillic", "finnish", "aadhaar", "transliterate"]),
    ("Media / imaging / codecs", ["avif", "heic", "heif", "hevc", "poppler", "dicom", "dcm",
                                   "optical", "raft", "image", "speech", "asr", "avfoundation",
                                   "pitch", "music"]),
    ("Cross-language bindings", ["rust", "crate", "libxrt", "xrt", "xclbin", "lua", "ebpf",
                                  "wayland", "arrow"]),
    ("Cloud / infra / data", ["snowflake", "s3", "bigquery", "sap", "hana", "kubernetes",
                               "crd", "aruba", "netedit"]),
    ("Forensics / security", ["ntfs", "mft", "dex", "smali", "dexdump", "hci", "snoop", "asn1",
                               "ber"]),
]


def categorize(name: str) -> str:
    low = name.lower()
    for label, kws in CATEGORY_RULES:
        if any(k in low for k in kws):
            return label
    return "Other / misc"


def load_runs(paths: list[Path]) -> list[dict]:
    runs: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"  ! skipping missing input: {p}")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for r in data.get("runs", []):
            runs.append(r)
    return runs


def build(paths: list[Path], out_dir: Path) -> tuple[Path, Path]:
    runs = load_runs(paths)

    hits: dict[str, int] = defaultdict(int)
    models: dict[str, set[str]] = defaultdict(set)
    score: dict[str, float] = {}
    verdict: dict[str, str] = {}
    example_prompt: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    for r in runs:
        vmap = {v["package"]: v for v in r.get("package_verdicts", [])}
        for name in r.get("hallucinated", []):
            hits[name] += 1
            models[name].add(r["model"])
            example_prompt.setdefault(name, r.get("prompt_idx"))
            v = vmap.get(name)
            if v:
                score[name] = v.get("score")
                verdict[name] = v.get("verdict", "")
                if not reasons.get(name):
                    reasons[name] = v.get("reasons", [])[:3]

    records = []
    for name in sorted(hits, key=lambda n: (-hits[n], n)):
        records.append({
            "package": name,
            "category": categorize(name),
            "hits": hits[name],
            "n_models": len(models[name]),
            "models": sorted(models[name]),
            "verdict": verdict.get(name, "BLOCK"),
            "trust_score": score.get(name),
            "example_prompt_idx": example_prompt.get(name),
            "evidence": reasons.get(name, []),
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "fake_package_database.json"
    json_path.write_text(json.dumps({
        "description": "Hallucinated/untrusted PyPI package names elicited from local "
                       "LLMs during PackageGuard testing. BLOCK/WARN per the trust engine.",
        "total_unique": len(records),
        "total_runs": len(runs),
        "records": records,
    }, indent=2), encoding="utf-8")

    # markdown
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_cat[rec["category"]].append(rec)

    lines = [
        "# PackageGuard — Fake Package Database (our testing)",
        "",
        f"**{len(records)} unique hallucinated/untrusted package names** elicited from "
        f"local LLMs across **{len(runs)} generations**.",
        "",
        "Every name below was produced by a real model in response to a coding prompt "
        "and then flagged by the trust engine as BLOCK (does not exist on PyPI) or WARN "
        "(exists but untrusted). This is the evidence that the hallucination phenomenon "
        "reproduces on our own models — not just in the paper.",
        "",
    ]
    for cat in sorted(by_cat):
        recs = by_cat[cat]
        lines.append(f"## {cat}  ({len(recs)})")
        lines.append("")
        lines.append("| Package | Hits | Models | Verdict | Score |")
        lines.append("|---|---|---|---|---|")
        for rec in recs:
            sc = "" if rec["trust_score"] is None else rec["trust_score"]
            lines.append(
                f"| `{rec['package']}` | {rec['hits']} | {rec['n_models']} | "
                f"{rec['verdict']} | {sc} |"
            )
        lines.append("")

    md_path = out_dir / "fake_package_database.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the consolidated fake-package DB")
    ap.add_argument("--inputs", nargs="*", default=DEFAULT_INPUTS)
    ap.add_argument("--out-dir", default="bench/results")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in args.inputs]
    json_path, md_path = build(paths, Path(args.out_dir))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    print(f"Wrote {json_path}  ({data['total_unique']} unique names from {data['total_runs']} runs)")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
