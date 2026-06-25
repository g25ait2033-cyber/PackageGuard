"""Install-time script scanner.

We download the package's sdist (or wheel if no sdist exists), open it
in-memory, and pattern-scan the install-time files for indicators of
package-based malware.

We DO NOT execute setup.py. Pattern match only.

Targets:
  setup.py, setup.cfg, pyproject.toml, MANIFEST.in, conftest.py

This is an ASYMMETRIC / veto signal. A clean scan is the expected baseline,
not evidence of trust, so it ABSTAINS (score=None, no vote). It only votes
when it actually finds suspicious install-time code, and that vote is a penalty:

  100  starting point once something is found
   -X  per category of suspicious pattern observed
    0  floor

Nothing to penalise (clean sdist, wheel-only, no artifact, not on PyPI) -> ABSTAIN.
"""

from __future__ import annotations

import io
import re
import tarfile
import zipfile
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 2.5
COLLECTOR_NAME = "installscan"

PYPI_JSON_URL = "https://pypi.org/pypi/{pkg}/json"
SCAN_FILES = ("setup.py", "setup.cfg", "pyproject.toml", "MANIFEST.in", "conftest.py")

# (label, regex, penalty)  — kept narrow to avoid false positives on legit packages.
PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("shell_exec",          re.compile(r"\bos\.system\s*\(|subprocess\.(?:run|call|Popen|check_output|check_call)\s*\("), 25),
    ("eval_exec",           re.compile(r"\b(?:eval|exec)\s*\("),                                                            20),
    ("compile_then_exec",   re.compile(r"\bcompile\s*\([^)]*\)\s*[,)]\s*exec"),                                            20),
    ("base64_decode_exec",  re.compile(r"base64\.b(?:64)?decode\s*\(.{0,200}?(?:exec|compile)\s*\(", re.DOTALL),           30),
    ("zlib_decompress_exec",re.compile(r"zlib\.decompress\s*\(.{0,200}?(?:exec|compile)\s*\(", re.DOTALL),                 30),
    ("install_time_network",re.compile(r"\b(?:urllib|urllib\.request|urllib2|httpx|requests)\b.{0,80}?\.(?:get|post|urlopen|Request)\s*\(", re.DOTALL), 20),
    ("ssh_writes",          re.compile(r"~/\.ssh/|/root/\.ssh/|authorized_keys|id_rsa"),                                   25),
    ("env_exfil",           re.compile(r"os\.environ(?:\.get)?\s*\(\s*['\"](?:AWS_|GH_TOKEN|GITHUB_TOKEN|SECRET|PASSWORD|API[_-]?KEY)"), 25),
    ("crontab_autostart",   re.compile(r"crontab\s*-|/etc/cron|registry\\\\Run|Startup\\\\"),                              20),
    ("dns_to_pastebin",     re.compile(r"\b(?:pastebin\.com|hastebin\.com|ngrok\.io|webhook\.site|requestbin)"),           25),
    ("raw_ip_url",          re.compile(r"https?://(?:\d{1,3}\.){3}\d{1,3}"),                                              20),
]

MAX_DOWNLOAD = 20 * 1024 * 1024  # 20 MB hard cap
MAX_FILE_SCAN = 1 * 1024 * 1024  # don't scan individual files > 1 MB


def analyze(
    package: str,
    ecosystem: str = "pypi",
    *,
    sdist_bytes: bytes | None = None,
    _fetch_pypi=None,
    _fetch_bytes=None,
) -> Signal:
    """Score install-time scripts for `package`.

    Test seams:
      - `sdist_bytes`: skip both HTTP calls; scan this tar/zip directly.
      - `_fetch_pypi`: replace the JSON-metadata fetch.
      - `_fetch_bytes`: replace the artifact-download fetch.
    """
    fetch_meta = _fetch_pypi or _default_fetch_pypi
    fetch_bytes = _fetch_bytes or _default_fetch_bytes
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")

    if sdist_bytes is None:
        # 1. find a download URL (prefer sdist)
        try:
            meta = fetch_meta(name)
        except Exception as e:
            return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name}, error=f"meta fetch failed: {e}")
        if meta is None:
            return Signal(COLLECTOR_NAME, None, WEIGHT, ["package not on PyPI (abstain)"], {"name": name})

        url, kind = _pick_download(meta)
        if not url:
            return Signal(
                COLLECTOR_NAME, None, WEIGHT,
                ["no downloadable artifact to scan (abstain)"],
                {"name": name, "artifact": None},
            )
        if kind == "wheel":
            return Signal(
                COLLECTOR_NAME, None, WEIGHT,
                ["wheel-only release — no install-time scripts to scan (abstain)"],
                {"name": name, "artifact": "wheel", "url": url},
            )

        try:
            sdist_bytes = fetch_bytes(url)
        except Exception as e:
            return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name, "url": url}, error=f"download failed: {e}")
        if sdist_bytes is None:
            return Signal(COLLECTOR_NAME, None, WEIGHT, ["artifact URL 404"], {"name": name, "url": url}, error="404 on artifact")

    findings = _scan_sdist(sdist_bytes)
    return _score(name, findings)


# ---------- internals ----------

def _pick_download(meta: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (url, kind) where kind in {'sdist','wheel'} preferring sdist."""
    urls = meta.get("urls") or []
    sdist = next((u for u in urls if u.get("packagetype") == "sdist"), None)
    if sdist and sdist.get("url"):
        return sdist["url"], "sdist"
    wheel = next((u for u in urls if u.get("packagetype") == "bdist_wheel"), None)
    if wheel and wheel.get("url"):
        return wheel["url"], "wheel"
    return None, None


def _scan_sdist(data: bytes) -> dict[str, list[str]]:
    """Return {pattern_label: [file_paths]} for every hit found."""
    findings: dict[str, list[str]] = {}
    for path, body in _iter_archive_files(data):
        base = path.rsplit("/", 1)[-1]
        if base not in SCAN_FILES:
            continue
        if len(body) > MAX_FILE_SCAN:
            continue
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            continue
        for label, pat, _penalty in PATTERNS:
            if pat.search(text):
                findings.setdefault(label, []).append(path)
    return findings


def _iter_archive_files(data: bytes):
    """Yield (path, bytes) from a .tar.gz, .tar, or .whl/.zip archive."""
    # try tar.gz / tar first
    bio = io.BytesIO(data)
    try:
        with tarfile.open(fileobj=bio, mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                yield member.name, f.read()
            return
    except (tarfile.TarError, OSError):
        bio.seek(0)
    # then zip (wheels)
    try:
        with zipfile.ZipFile(bio) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                yield info.filename, zf.read(info)
    except zipfile.BadZipFile:
        return


def _score(name: str, findings: dict[str, list[str]]) -> Signal:
    if not findings:
        # Clean scan is the expected baseline, not evidence of trust. Abstain so
        # a fresh package can't earn a high score merely by shipping no malware.
        return Signal(
            collector=COLLECTOR_NAME,
            score=None,
            weight=WEIGHT,
            reasons=["no suspicious install-time patterns (abstain)"],
            raw={"name": name, "findings": {}, "abstained": True},
        )
    score = 100.0
    reasons: list[str] = []
    for label, pat, penalty in PATTERNS:
        if label in findings:
            score -= penalty
            files = findings[label]
            reasons.append(f"{label} in {len(files)} file(s)")
    score = max(0.0, min(100.0, score))
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={"name": name, "findings": findings},
    )


def _default_fetch_pypi(name: str) -> dict | None:
    return http.get_json(http.pypi_json_url(name))


def _default_fetch_bytes(url: str) -> bytes | None:
    return http.get_bytes(url, max_bytes=MAX_DOWNLOAD)
