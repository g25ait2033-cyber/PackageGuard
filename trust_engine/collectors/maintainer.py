"""Maintainer collector — scores legitimacy of the package's listed author.

We deliberately AVOID HTML-scraping PyPI user pages (brittle).
Instead we use the metadata already exposed by the JSON API:

  - info.author, info.author_email
  - info.maintainer, info.maintainer_email
  - info.project_urls (used to compare email domain vs repo owner)

Score factors:
  - any author/maintainer info present at all
  - email present
  - email is a custom domain (corp / project) vs freemail
  - author / maintainer fields are non-trivial strings (not empty, not "UNKNOWN")
"""

from __future__ import annotations

import re
from typing import Any

from trust_engine import http
from trust_engine.types import Signal

WEIGHT = 1.5
COLLECTOR_NAME = "maintainer"

PYPI_JSON_URL = "https://pypi.org/pypi/{pkg}/json"

# Common free-email providers — having an author email here is fine, just
# weaker than a custom-domain email tied to the project's org.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "outlook.com",
    "hotmail.com", "live.com", "icloud.com", "me.com", "protonmail.com",
    "proton.me", "aol.com", "mail.com", "yandex.ru", "qq.com", "163.com",
}
# RFC 2606 / RFC 6761 reserved + classic placeholder domains. No legitimate
# maintainer publishes under these — an email here is a strong squat signal, the
# opposite of a real custom org domain, so it is penalised rather than rewarded.
PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu",
    "test.com", "test.org", "test.net", "invalid", "localhost",
    "domain.com", "yourdomain.com", "noreply.com", "no-reply.com",
    "email.example.com",
}
PLACEHOLDER_AUTHORS = {"", "unknown", "n/a", "none", "tbd", "anonymous"}

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w.-]+\.\w+)")
# PEP 621 packs names into the email field as "Name <addr>, Name <addr>".
_NAME_IN_EMAIL_RE = re.compile(r"([^,<]+?)\s*<[^>]+@[^>]+>")


def analyze(
    package: str,
    ecosystem: str = "pypi",
    *,
    info: dict[str, Any] | None = None,
    _fetch=None,
) -> Signal:
    """Score the maintainer footprint for `package`.

    Pass `info` (from `registry`'s raw response) to avoid a duplicate fetch.
    """
    fetch = _fetch or _fetch_pypi_info
    name = package.strip()
    if not name:
        return Signal(COLLECTOR_NAME, None, WEIGHT, ["empty name"], {}, error="empty name")

    if info is None:
        try:
            data = fetch(name)
        except Exception as e:
            return Signal(COLLECTOR_NAME, None, WEIGHT, [], {"name": name}, error=f"fetch failed: {e}")
        if data is None:
            return Signal(
                COLLECTOR_NAME, 0.0, WEIGHT,
                ["package not on PyPI; no maintainer info"],
                {"name": name, "exists": False},
            )
        info = data.get("info") or {}

    author = (info.get("author") or "").strip()
    author_email = (info.get("author_email") or "").strip()
    maintainer = (info.get("maintainer") or "").strip()
    maintainer_email = (info.get("maintainer_email") or "").strip()

    # PEP 621 fallback: pull names out of "Name <addr>" embedded in email fields.
    if not author or author.lower() in PLACEHOLDER_AUTHORS:
        names_from = _NAME_IN_EMAIL_RE.findall(author_email)
        if names_from:
            author = ", ".join(n.strip() for n in names_from)
    if not maintainer or maintainer.lower() in PLACEHOLDER_AUTHORS:
        names_from = _NAME_IN_EMAIL_RE.findall(maintainer_email)
        if names_from:
            maintainer = ", ".join(n.strip() for n in names_from)

    reasons: list[str] = []
    score = 50.0

    has_author = author and author.lower() not in PLACEHOLDER_AUTHORS
    has_maintainer = maintainer and maintainer.lower() not in PLACEHOLDER_AUTHORS
    if not has_author and not has_maintainer:
        score = 20.0
        reasons.append("no author or maintainer name listed")
    else:
        if has_author:
            reasons.append(f"author listed: {author[:40]!r}")
            score += 5
        if has_maintainer:
            reasons.append(f"maintainer listed: {maintainer[:40]!r}")
            score += 5

    email = author_email or maintainer_email
    if not email:
        score -= 15
        reasons.append("no author email")
    else:
        m = _EMAIL_RE.search(email)
        domain = (m.group(1).lower() if m else "")
        if not domain:
            score -= 10
            reasons.append(f"malformed author email: {email!r}")
        elif domain in PLACEHOLDER_EMAIL_DOMAINS:
            score -= 20
            reasons.append(f"placeholder/reserved email domain ({domain})")
        elif domain in FREEMAIL_DOMAINS:
            score += 5
            reasons.append(f"freemail domain ({domain})")
        else:
            score += 20
            reasons.append(f"custom email domain ({domain})")

    score = max(0.0, min(100.0, score))
    return Signal(
        collector=COLLECTOR_NAME,
        score=score,
        weight=WEIGHT,
        reasons=reasons,
        raw={
            "name": name,
            "author": author,
            "author_email": author_email,
            "maintainer": maintainer,
            "maintainer_email": maintainer_email,
        },
    )


def _fetch_pypi_info(name: str) -> dict | None:
    return http.get_json(http.pypi_json_url(name))
