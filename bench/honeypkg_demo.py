"""Honey-package demo: prove the engine blocks a hallucinated name that EXISTS.

The slopsquatting attack from the paper is a hallucinated package name that an
adversary has ALREADY PUBLISHED. A naive existence/allow-list check waves it
through because the name now resolves. This script demonstrates that the full
PackageGuard engine still returns BLOCK/WARN even when the package genuinely
exists on a live index.

To stay ethical we point the engine at TestPyPI (an isolated sandbox index that
is NOT the real PyPI) via PACKAGEGUARD_PYPI_BASE. Upload the harmless honey
package in bench/honeypkg/ to TestPyPI first (see the run steps), then:

    python bench/honeypkg_demo.py pybloomberg

It compares two worlds for the same name:
    * registry-only (allow-list)  : exists -> PASS
    * full engine (8 collectors)  : exists=True but verdict=BLOCK/WARN

Options:
    --base URL   index base to resolve against (default https://test.pypi.org)
    --real       shortcut to point at real https://pypi.org (for contrast only)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trust_engine.score import evaluate  # noqa: E402

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

VERDICT_COLOR = {"PASS": GREEN, "WARN": YELLOW, "BLOCK": RED}


def _enable_ansi() -> None:
    if os.name == "nt":
        os.system("")  # turn on ANSI escape processing in cmd.exe


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("package", help="package name to evaluate (e.g. pybloomberg)")
    ap.add_argument("--base", default="https://test.pypi.org",
                    help="index base URL to resolve against (default: TestPyPI)")
    ap.add_argument("--real", action="store_true",
                    help="resolve against real https://pypi.org instead (contrast only)")
    args = ap.parse_args()

    _enable_ansi()
    base = "https://pypi.org" if args.real else args.base
    os.environ["PACKAGEGUARD_PYPI_BASE"] = base

    print(f"\n{BOLD}PackageGuard honey-package demo{RESET}")
    print(f"  index   : {base}")
    print(f"  package : {args.package}")
    print(f"  {GREY}(cache disabled so this reflects the live index right now){RESET}\n")

    # use_cache=False: the SQLite cache is keyed by name only and would otherwise
    # collide with a previous real-PyPI lookup of the same name.
    report = evaluate(args.package, "pypi", use_cache=False)

    exists = None
    print(f"  {BOLD}per-collector signals{RESET}")
    for s in report.signals:
        score = "  -  " if s.score is None else f"{s.score:5.1f}"
        note = s.error if s.error else "; ".join(s.reasons[:2])
        print(f"    {s.collector:12} w={s.weight:<4} score={score}  {GREY}{note}{RESET}")
        if s.collector == "registry" and s.raw:
            exists = s.raw.get("exists")

    reg_only = "PASS" if exists else ("BLOCK" if exists is False else "UNKNOWN")
    vc = VERDICT_COLOR.get(report.verdict, "")
    rc = VERDICT_COLOR.get(reg_only, "")

    print(f"\n  {BOLD}exists on this index{RESET} : {exists}")
    print(f"  {BOLD}registry-only (allow-list){RESET} : {rc}{reg_only}{RESET}")
    print(f"  {BOLD}full engine (8 collectors){RESET} : {vc}{report.verdict}  "
          f"(score {report.score}){RESET}")

    print()
    if exists and report.verdict in ("BLOCK", "WARN"):
        print(f"  {GREEN}{BOLD}>>> SLOPSQUAT DEFEATED:{RESET} the name EXISTS on a live index, so a")
        print(f"      naive allow-list says PASS — but the full engine says "
              f"{vc}{report.verdict}{RESET}.")
        print(f"      Existence alone is not trust. This is the result an allow-list cannot give.")
    elif exists is False:
        print(f"  {YELLOW}Note:{RESET} this name does not exist on {base} yet.")
        print(f"      Upload the honey package in bench/honeypkg/ to TestPyPI first, then re-run.")
    else:
        print(f"  {GREY}(engine PASSed it — pick a fresher/lower-signal name to demonstrate the gap){RESET}")
    print()


if __name__ == "__main__":
    main()
