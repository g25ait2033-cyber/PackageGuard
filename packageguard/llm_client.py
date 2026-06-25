"""Thin wrapper around openai SDK pointed at local LM Studio / Ollama / real cloud.

Single shared client so the proxy, elicitor, and bench all behave identically.
"""

from __future__ import annotations

import os
from typing import Iterable

from openai import OpenAI


def make_client(*, base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    """Build an OpenAI client. Defaults to LM Studio on localhost:1234."""
    return OpenAI(
        base_url=base_url or os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=api_key or os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
    )


def make_ollama_client(*, base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    """Build an OpenAI client pointed at Ollama's OpenAI-compatible endpoint (:11434)."""
    return OpenAI(
        base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key=api_key or os.getenv("OLLAMA_API_KEY", "ollama"),
    )


def list_models(client: OpenAI) -> list[str]:
    return [m.id for m in client.models.list().data]


def chat(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict],
    temperature: float = 1.0,
    max_tokens: int = 1024,
    n: int = 1,
) -> list[str]:
    """Return n completion text strings."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        n=n,
    )
    return [c.message.content or "" for c in resp.choices]
