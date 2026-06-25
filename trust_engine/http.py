"""Shared HTTP helper used by all network-based collectors.

Wraps httpx with:
  - sensible timeout
  - polite User-Agent
  - retry-on-transient-error via tenacity (network/5xx only, never on 4xx)
"""

from __future__ import annotations

import os

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

USER_AGENT = "PackageGuard/0.1 (+research; contact: sduggisetty)"
DEFAULT_TIMEOUT = 10.0

# Which package index to resolve names against. Defaults to real PyPI, but can be
# pointed at TestPyPI (https://test.pypi.org) via PACKAGEGUARD_PYPI_BASE for the
# honey-package experiment — an isolated live index, never the real ecosystem.
DEFAULT_PYPI_BASE = "https://pypi.org"


def pypi_base() -> str:
    return os.environ.get("PACKAGEGUARD_PYPI_BASE", DEFAULT_PYPI_BASE).rstrip("/")


def pypi_json_url(name: str) -> str:
    return f"{pypi_base()}/pypi/{name}/json"


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception(_is_transient),
)
def get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: dict | None = None) -> dict | None:
    """GET a URL and return parsed JSON, or None on 404. Raises on other 4xx/5xx after retries."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=h)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception(_is_transient),
)
def post_json(
    url: str,
    payload: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict | None = None,
) -> dict:
    """POST JSON and return parsed JSON. Raises on 4xx/5xx after retries."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.post(url, json=payload, headers=h)
        r.raise_for_status()
        return r.json()


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception(_is_transient),
)
def get_bytes(url: str, *, timeout: float = 30.0, max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """GET a URL and return raw bytes. Returns None on 404. Caps body at max_bytes."""
    h = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=h)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.content
        if len(body) > max_bytes:
            raise ValueError(f"response too large: {len(body)} bytes")
        return body
