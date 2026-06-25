"""SQLite TTL cache for TrustReports.

A trust evaluation makes 5-7 HTTP calls. Caching turns repeat lookups into
sub-millisecond reads, which matters for the live LLM proxy.

Schema is intentionally tiny: (ecosystem, package, ts, json_blob).
Default TTL: 6 hours.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from trust_engine.types import Signal, TrustReport

DEFAULT_TTL_SECONDS = 6 * 3600
DEFAULT_PATH = os.environ.get("PACKAGEGUARD_CACHE", ".cache/trust_cache.sqlite")


def _conn(path: str | os.PathLike) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trust_cache (
            ecosystem TEXT NOT NULL,
            package   TEXT NOT NULL,
            ts        INTEGER NOT NULL,
            data      TEXT NOT NULL,
            PRIMARY KEY (ecosystem, package)
        )
        """
    )
    return conn


def get(package: str, ecosystem: str = "pypi", *, ttl: int = DEFAULT_TTL_SECONDS, path: str | os.PathLike | None = None) -> TrustReport | None:
    path = path or DEFAULT_PATH
    with _conn(path) as conn:
        row = conn.execute(
            "SELECT ts, data FROM trust_cache WHERE ecosystem=? AND package=?",
            (ecosystem, package.lower()),
        ).fetchone()
    if not row:
        return None
    ts, data = row
    if time.time() - ts > ttl:
        return None
    return _from_json(json.loads(data))


def put(report: TrustReport, *, path: str | os.PathLike | None = None) -> None:
    path = path or DEFAULT_PATH
    blob = json.dumps(_to_json(report))
    with _conn(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trust_cache(ecosystem, package, ts, data) VALUES (?,?,?,?)",
            (report.ecosystem, report.package.lower(), int(time.time()), blob),
        )
        conn.commit()


def clear(*, path: str | os.PathLike | None = None) -> None:
    path = path or DEFAULT_PATH
    with _conn(path) as conn:
        conn.execute("DELETE FROM trust_cache")
        conn.commit()


def _to_json(r: TrustReport) -> dict:
    return {
        "package": r.package,
        "ecosystem": r.ecosystem,
        "score": r.score,
        "verdict": r.verdict,
        "signals": [asdict(s) for s in r.signals],
    }


def _from_json(d: dict) -> TrustReport:
    return TrustReport(
        package=d["package"],
        ecosystem=d["ecosystem"],
        score=d["score"],
        verdict=d["verdict"],
        signals=[Signal(**s) for s in d.get("signals", [])],
    )
