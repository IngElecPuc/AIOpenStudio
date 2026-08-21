"""Pure parsing for the editable LLM generation controls."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from aiopenstudio.core.contracts import (
    ChatOptions,
    ContextOverflowPolicy,
    StructuredOutputMode,
    StructuredOutputSpec,
)

THINK_DEFAULT = "Predeterminado"
THINK_OFF = "Desactivar"
THINK_ON = "Activar"
THINK_LOW = "Bajo"
THINK_MEDIUM = "Medio"
THINK_HIGH = "Alto"

OUTPUT_TEXT = "Texto"
OUTPUT_JSON = "JSON"
OUTPUT_SCHEMA = "JSON Schema"

OVERFLOW_REJECT = "Rechazar"
OVERFLOW_TRUNCATE = "Truncar historial antiguo"


class GenerationSelection(BaseModel):
    options: ChatOptions
    system_prompt: str | None = None
    overflow_policy: ContextOverflowPolicy
    think: bool | Literal["low", "medium", "high"] | None = None
    output: StructuredOutputSpec


def parse_generation_selection(
    values: Mapping[str, str],
    *,
    system_prompt: str,
    stop_sequences: str,
    thinking: str,
    overflow: str,
    output_mode: str,
    json_schema: str,
) -> GenerationSelection:
    """Convert form strings, where blank means do not override the model."""
    options = ChatOptions(
        temperature=_optional_float(values.get("temperature"), "Temperatura"),
        top_p=_optional_float(values.get("top_p"), "top_p"),
        top_k=_optional_int(values.get("top_k"), "top_k"),
        min_p=_optional_float(values.get("min_p"), "min_p"),
        seed=_optional_int(values.get("seed"), "Seed"),
        context_length=_optional_int(values.get("context_length"), "Ventana de contexto"),
        max_new_tokens=_optional_int(values.get("max_new_tokens"), "Máximo de tokens"),
        repeat_penalty=_optional_float(
            values.get("repeat_penalty"),
            "Penalización de repetición",
        ),
        stop=tuple(line.strip() for line in stop_sequences.splitlines() if line.strip()),
    )
    think = _thinking_value(thinking)
    overflow_policy = (
        ContextOverflowPolicy.TRUNCATE_OLDEST
        if overflow == OVERFLOW_TRUNCATE
        else ContextOverflowPolicy.REJECT
    )
    output = _output_spec(output_mode, json_schema)
    normalized_system = system_prompt.strip()
    return GenerationSelection(
        options=options,
        system_prompt=normalized_system or None,
        overflow_policy=overflow_policy,
        think=think,
        output=output,
    )


def _optional_float(raw: str | None, label: str) -> float | None:
    normalized = (raw or "").strip()
    if not normalized:
        return None
    try:
        return float(normalized.replace(",", "."))
    except ValueError as error:
        raise ValueError(f"{label} debe ser un número o quedar vacío.") from error


def _optional_int(raw: str | None, label: str) -> int | None:
    normalized = (raw or "").strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError as error:
        raise ValueError(f"{label} debe ser un entero o quedar vacío.") from error


def _thinking_value(
    selection: str,
) -> bool | Literal["low", "medium", "high"] | None:
    values: dict[str, bool | Literal["low", "medium", "high"] | None] = {
        THINK_DEFAULT: None,
        THINK_OFF: False,
        THINK_ON: True,
        THINK_LOW: "low",
        THINK_MEDIUM: "medium",
        THINK_HIGH: "high",
    }
    if selection not in values:
        raise ValueError("La opción de razonamiento no es válida.")
    return values[selection]


def _output_spec(selection: str, raw_schema: str) -> StructuredOutputSpec:
    if selection == OUTPUT_TEXT:
        return StructuredOutputSpec()
    if selection == OUTPUT_JSON:
        return StructuredOutputSpec(mode=StructuredOutputMode.JSON)
    if selection != OUTPUT_SCHEMA:
        raise ValueError("El formato de salida no es válido.")
    if len(raw_schema) > 100_000:
        raise ValueError("El JSON Schema supera el límite de 100.000 caracteres.")
    try:
        schema = json.loads(raw_schema)
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON Schema inválido: {error.msg} (línea {error.lineno}).") from error
    if not isinstance(schema, dict):
        raise ValueError("El JSON Schema debe ser un objeto JSON.")
    return StructuredOutputSpec(
        mode=StructuredOutputMode.JSON_SCHEMA,
        json_schema=schema,
    )
