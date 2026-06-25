"""OSV collector — queries https://osv.dev for known vulnerabilities.

OSV.dev is Google's open vulnerability database. We POST a small JSON
payload with the package name + ecosystem and get back a list of vulns.

We query by name only, so OSV returns the package's *entire historical*
advisory list across all versions. Old, popular packages (e.g. Pillow) can
therefore show dozens of long-since-patched CRITICALs, which would unfairly
tank an otherwise healthy package. To score *current* risk rather than ancient
history, we split advisories into:
  - unpatched: no fixed version is published yet (the real, present-day risk)
  - patched:   a fixed version exists (historical; informational only)
and base the score on the *unpatched* set.

This is an ASYMMETRIC / veto signal. A clean record (no open vulnerabilities)
is the expected baseline, not a trust boost, so OSV ABSTAINS (score=None, no
vote) rather than award points. It only votes when there is open, present-day
risk, and in that case it votes with a raised weight (OPEN_VULN_WEIGHT) so an
open advisory can pull the verdict down hard.

Score logic (only when UNPATCHED vulns exist):
  only LOW                  ->  85
  has at least one MEDIUM   ->  70
  has at least one HIGH     ->  40
  has at least one CRITICAL ->  10
  >=2 CRITICAL              ->   0
  no open vulns (none, or all patched) -> ABSTAIN (no vote)
"""

from __future__ import annotations

import re
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 2.0
# When OSV finds OPEN (unpatched) vulnerabilities it votes with a raised weight
# so present-day risk takes priority over the other trust signals.
OPEN_VULN_WEIGHT = 4.0
COLLECTOR_NAME = "osv"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"

ECOSYSTEM_MAP = {"pypi": "PyPI", "npm": "npm"}

_CVSS_SCORE_RE = re.compile(r"CVSS:[^/]+/.*?/A:[NLH]")  # tolerate v3 / v4 strings


def analyze(package: str, ecosystem: str = "pypi", *, _fetch=None) -> Signal:
    """Query OSV.dev for vulnerabilities affecting `package`."""
    fetch = _fetch or _fetch_osv
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")

    eco = ECOSYSTEM_MAP.get(ecosystem.lower(), ecosystem)
    try:
        data = fetch(name, eco)
    except Exception as e:
        return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name}, error=f"fetch failed: {e}")

    vulns = (data or {}).get("vulns", []) or []
    if not vulns:
        # Clean is the baseline, not a trust boost -> abstain (no vote).
        return Signal(
            collector=COLLECTOR_NAME,
            score=None,
            weight=WEIGHT,
            reasons=["no known vulnerabilities on OSV.dev (abstain)"],
            raw={"name": name, "ecosystem": eco, "vuln_count": 0, "abstained": True},
        )

    # Split into present-day risk (unpatched) vs historical (a fix exists).
    unpatched = [v for v in vulns if not _has_fix(v)]
    patched_count = len(vulns) - len(unpatched)

    total_counts = _severity_counts(vulns)
    unpatched_counts = _severity_counts(unpatched)
    ids = [v.get("id") for v in vulns if v.get("id")]

    if not unpatched:
        # Every known advisory has a published fix — no open risk. Abstain so a
        # clean-but-historical record neither rewards nor penalises the package.
        return Signal(
            collector=COLLECTOR_NAME,
            score=None,
            weight=WEIGHT,
            reasons=[f"{len(vulns)} known vuln(s), all patched — no open risk (abstain)"],
            raw={
                "name": name,
                "ecosystem": eco,
                "vuln_count": len(vulns),
                "unpatched_count": 0,
                "patched_count": patched_count,
                "severity_counts": total_counts,
                "unpatched_severity_counts": unpatched_counts,
                "ids": ids,
                "abstained": True,
            },
        )

    # Open, unpatched vulnerabilities = present-day risk. This is the only case
    # OSV votes, and it votes with a raised weight so an open advisory can pull
    # the verdict down hard regardless of the other trust signals.
    score = _score_from_counts(unpatched_counts)
    reasons = [
        f"{len(unpatched)} unpatched vuln(s): "
        + ", ".join(f"{k}={v}" for k, v in unpatched_counts.items() if v)
    ]
    if patched_count:
        reasons.append(f"{patched_count} additional vuln(s) already patched")
    unpatched_ids = [v.get("id") for v in unpatched if v.get("id")]
    if unpatched_ids[:3]:
        reasons.append("e.g. " + ", ".join(unpatched_ids[:3]))

    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=OPEN_VULN_WEIGHT,
        reasons=reasons,
        raw={
            "name": name,
            "ecosystem": eco,
            "vuln_count": len(vulns),
            "unpatched_count": len(unpatched),
            "patched_count": patched_count,
            "severity_counts": total_counts,
            "unpatched_severity_counts": unpatched_counts,
            "ids": ids,
        },
    )


def _severity_counts(vulns: list[dict[str, Any]]) -> dict[str, int]:
    severities = [_severity_of(v) for v in vulns]
    return {s: severities.count(s) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")}


def _has_fix(vuln: dict[str, Any]) -> bool:
    """True if OSV lists a published fixed version for this advisory.

    OSV encodes fixes as a `fixed` event inside affected[].ranges[].events.
    A `fixed` (or `last_affected`) event means a remediated release exists, so
    the advisory is historical rather than an open, present-day risk.
    """
    for aff in vuln.get("affected", []) or []:
        for rng in aff.get("ranges", []) or []:
            for ev in rng.get("events", []) or []:
                if isinstance(ev, dict) and ("fixed" in ev or "last_affected" in ev):
                    return True
    return False



def _score_from_counts(c: dict[str, int]) -> float:
    if c.get("CRITICAL", 0) >= 2:
        return 0.0
    if c.get("CRITICAL", 0) >= 1:
        return 10.0
    if c.get("HIGH", 0) >= 1:
        return 40.0
    if c.get("MEDIUM", 0) >= 1:
        return 70.0
    if c.get("LOW", 0) >= 1:
        return 85.0
    return 60.0  # has unknown-severity vulns; conservative


def _severity_of(vuln: dict[str, Any]) -> str:
    """Return CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN.

    OSV exposes severity in two places:
      - vuln['severity']  -> list of {'type': 'CVSS_V3', 'score': '<vector>'}
      - vuln['database_specific']['severity'] -> string label
    """
    db_sev = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(db_sev, str):
        s = db_sev.upper()
        if s in ("CRITICAL", "HIGH", "MEDIUM", "MODERATE", "LOW"):
            return "MEDIUM" if s == "MODERATE" else s

    for entry in vuln.get("severity", []) or []:
        score_str = entry.get("score", "")
        # OSV exposes the CVSS *vector* (e.g. "CVSS:3.1/AV:N/AC:L/...") and sometimes
        # appends the numeric base score. Strip the "CVSS:<ver>" prefix so we don't
        # mistake the version (e.g. 3.1) for the score.
        cleaned = re.sub(r"^CVSS:\d+\.\d+", "", score_str)
        nums = [float(m) for m in re.findall(r"\b(\d+\.\d)\b", cleaned)]
        if nums:
            return _cvss_label(max(nums))
    return "UNKNOWN"


def _cvss_label(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "UNKNOWN"


def _fetch_osv(name: str, ecosystem: str) -> dict:
    payload = {"package": {"name": name, "ecosystem": ecosystem}}
    return http.post_json(OSV_QUERY_URL, payload)
