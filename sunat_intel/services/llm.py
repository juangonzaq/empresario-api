"""Thin OpenAI wrapper for the intelligence services.

The API key comes exclusively from the backend environment (``OPENAI_API_KEY``
in ``.env``); it is never exposed through any endpoint. Every call uses strict
structured outputs so responses are machine-parseable JSON, never free prose.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from openai import OpenAI

INTEL_MODEL = os.getenv("OPENAI_INTEL_MODEL", "gpt-5-mini")


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI()


def structured_messages(
    messages: list[dict[str, str]],
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Run one completion over a full message list constrained to ``schema``."""
    response = get_client().chat.completions.create(
        model=INTEL_MODEL,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    return json.loads(response.choices[0].message.content or "{}")


def structured_completion(
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Single-turn variant of :func:`structured_messages`."""
    return structured_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        schema_name,
        schema,
    )
