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


def structured_with_tools(
    messages: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    tools: list[dict[str, Any]],
    executors: dict[str, Any],
    max_rounds: int = 4,
) -> dict[str, Any]:
    """Como :func:`structured_messages`, pero el modelo puede ejecutar
    consultas registradas antes de responder.

    El bucle es acotado: hasta ``max_rounds`` tandas de consultas y una
    respuesta final. Un error en una consulta (parámetros inválidos, p. ej.)
    vuelve al modelo como ``{"error": …}`` para que lo corrija o responda sin
    ese dato — nunca tumba la pregunta.
    """
    convo = list(messages)
    formato = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": schema},
    }
    for _ in range(max_rounds):
        response = get_client().chat.completions.create(
            model=INTEL_MODEL, messages=convo, tools=tools,
            response_format=formato,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            return json.loads(message.content or "{}")
        convo.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in message.tool_calls],
        })
        for tc in message.tool_calls:
            fn = executors.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = fn(**args) if fn else {"error": "Consulta desconocida."}
            except Exception as exc:  # noqa: BLE001 — el modelo decide qué hacer
                result = {"error": str(exc)}
            convo.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })
    # Tandas agotadas: última llamada sin herramientas para forzar la respuesta.
    response = get_client().chat.completions.create(
        model=INTEL_MODEL, messages=convo, response_format=formato,
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
