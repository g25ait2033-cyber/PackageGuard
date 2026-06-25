"""Quick summary of all elicitation result JSONs in bench/results/."""
from __future__ import annotations

import glob
import json
import os
from collections import Counter

files = sorted(glob.glob(os.path.join("bench", "results", "elicitation_*.json")))
if not files:
    print("no result files found in bench/results/")
    raise SystemExit(0)

for f in files:
    o = json.load(open(f, encoding="utf-8"))
    runs = o["runs"]
    with_hall = [r for r in runs if r["hallucinated"]]
    hall_counter = Counter(h for r in runs for h in r["hallucinated"])
    print("\n===", os.path.basename(f), "===")
    print("models      :", o["models_used"])
    print("params      :", o["params"])
    print("prompts     :", o["prompt_count"], " total_runs:", o["total_runs"])
    print(f"runs w/ hall: {len(with_hall)}/{len(runs)} "
          f"({100*len(with_hall)/len(runs):.1f}%)" if runs else "no runs")
    print("uniq hall   :", len(hall_counter))
    for name, c in hall_counter.most_common():
        print(f"   {c:>3}x  {name}")
