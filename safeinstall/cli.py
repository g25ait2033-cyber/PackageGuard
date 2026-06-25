"""safeinstall — Tier 2 wrapper around `pip install`.

Usage:
  safeinstall pip install <pkg1> [<pkg2> ...]
  safeinstall check <pkg1> [<pkg2> ...]   # check only, don't install
  safeinstall --json check <pkg>          # machine-readable

Behavior:
  - For each package: run the trust engine.
  - PASS  -> proceed.
  - WARN  -> ask the user (skip with --yes / fail with --strict).
  - BLOCK -> refuse, exit code 2.
  - All PASSed (or user-approved) packages handed to the real `pip install`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Sequence

from rich.console import Console
from rich.table import Table

from trust_engine.score import evaluate
from trust_engine.types import TrustReport

console = Console()

VERDICT_COLOR = {"PASS": "green", "WARN": "yellow", "BLOCK": "red"}
EXIT_BLOCKED = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="safeinstall", description="Trust-engine wrapper around pip install")
    parser.add_argument("--json", action="store_true", help="emit JSON report and exit (check mode only)")
    parser.add_argument("--yes", "-y", action="store_true", help="auto-approve WARN packages")
    parser.add_argument("--strict", action="store_true", help="treat WARN as BLOCK")
    parser.add_argument("--no-cache", action="store_true", help="bypass the trust cache")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="evaluate packages without installing")
    p_check.add_argument("packages", nargs="+")

    p_pip = sub.add_parser("pip", help="wrap a pip subcommand")
    p_pip.add_argument("pip_cmd", choices=["install"])
    p_pip.add_argument("packages", nargs="+")

    args = parser.parse_args(argv)
    packages = args.packages
    reports = [evaluate(p, "pypi", use_cache=not args.no_cache) for p in packages]

    if args.json:
        print(json.dumps([_report_to_dict(r) for r in reports], indent=2))
        return _exit_code(reports, args)

    _print_reports(reports)

    blocked = [r for r in reports if r.verdict == "BLOCK"]
    warned = [r for r in reports if r.verdict == "WARN"]

    if blocked:
        console.print(f"[bold red]BLOCKED:[/] {', '.join(r.package for r in blocked)}")
        return EXIT_BLOCKED

    if warned:
        if args.strict:
            console.print(f"[bold red]--strict: refusing WARN packages:[/] {', '.join(r.package for r in warned)}")
            return EXIT_BLOCKED
        if not args.yes:
            console.print(f"[bold yellow]WARN:[/] {', '.join(r.package for r in warned)}")
            ans = input("Proceed with install? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                return EXIT_BLOCKED

    if args.cmd == "check":
        return 0

    # cmd == "pip install"
    pip_args = [sys.executable, "-m", "pip", "install", *packages]
    console.print(f"[bold]Running:[/] {' '.join(pip_args)}")
    return subprocess.call(pip_args)


def _print_reports(reports: list[TrustReport]) -> None:
    table = Table(title="PackageGuard Trust Report")
    table.add_column("package")
    table.add_column("score", justify="right")
    table.add_column("verdict")
    table.add_column("top reasons")
    for r in reports:
        verdict_str = f"[bold {VERDICT_COLOR.get(r.verdict, 'white')}]{r.verdict}[/]"
        top = _top_reasons(r)
        table.add_row(r.package, f"{r.score:.1f}", verdict_str, top)
    console.print(table)


def _top_reasons(r: TrustReport, n: int = 3) -> str:
    """Show the lowest-scoring collectors' first reason each."""
    items = sorted(
        (s for s in r.signals if s.score is not None),
        key=lambda s: s.score,
    )
    out = []
    for s in items[:n]:
        first = s.reasons[0] if s.reasons else ""
        out.append(f"[{s.collector} {s.score:.0f}] {first}")
    return "\n".join(out)


def _exit_code(reports: list[TrustReport], args) -> int:
    blocked = any(r.verdict == "BLOCK" for r in reports)
    warned = any(r.verdict == "WARN" for r in reports)
    if blocked:
        return EXIT_BLOCKED
    if warned and args.strict:
        return EXIT_BLOCKED
    return 0


def _report_to_dict(r: TrustReport) -> dict:
    return {
        "package": r.package,
        "ecosystem": r.ecosystem,
        "score": r.score,
        "verdict": r.verdict,
        "signals": [
            {
                "collector": s.collector,
                "score": s.score,
                "weight": s.weight,
                "reasons": s.reasons,
                "error": s.error,
            }
            for s in r.signals
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
