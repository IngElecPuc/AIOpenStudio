"""Local validation for JSON responses requested from an LLM runtime."""

from __future__ import annotations

import re
from typing import Any

from pydantic import JsonValue, TypeAdapter, ValidationError

from aiopenstudio.core.contracts import StructuredOutputMode, StructuredOutputSpec

_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    }
)


def validate_structured_response(response: str, spec: StructuredOutputSpec) -> JsonValue | None:
    """Parse JSON with Pydantic and validate the documented schema subset."""
    if spec.mode is StructuredOutputMode.TEXT:
        return None
    try:
        value = _JSON_ADAPTER.validate_json(response)
    except ValidationError as error:
        raise ValueError("La respuesta no es JSON válido.") from error
    if spec.mode is StructuredOutputMode.JSON_SCHEMA:
        schema = spec.json_schema
        if schema is None:  # Guarded by the contract; keeps the service defensive.
            raise ValueError("No se recibió el esquema solicitado.")
        _validate_schema_definition(schema, path="$schema")
        _validate_value(value, schema, path="$")
    return value


def _validate_schema_definition(schema: object, *, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} debe ser un objeto JSON.")
    unsupported = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"{path} usa palabras de esquema no admitidas: {names}.")
    expected = schema.get("type")
    if expected not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        raise ValueError(f"{path}.type no está admitido.")
    properties = schema.get("properties")
    if properties is not None:
        if expected != "object" or not isinstance(properties, dict):
            raise ValueError(f"{path}.properties sólo es válido para un objeto.")
        for name, child in properties.items():
            if not isinstance(name, str):
                raise ValueError(f"{path}.properties contiene una clave no textual.")
            _validate_schema_definition(child, path=f"{path}.properties.{name}")
    required = schema.get("required")
    if required is not None and (
        expected != "object"
        or not isinstance(required, list)
        or not all(isinstance(name, str) for name in required)
    ):
        raise ValueError(f"{path}.required debe ser una lista de nombres para un objeto.")
    items = schema.get("items")
    if items is not None:
        if expected != "array":
            raise ValueError(f"{path}.items sólo es válido para un array.")
        _validate_schema_definition(items, path=f"{path}.items")


def _validate_value(value: JsonValue, schema: dict[str, Any], *, path: str) -> None:
    expected = schema["type"]
    if not _matches_type(value, expected):
        raise ValueError(f"{path} no cumple el tipo {expected}.")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} no coincide con el valor constante requerido.")
    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            raise ValueError(f"El enum de {path} debe ser una lista.")
        if value not in enum:
            raise ValueError(f"{path} no pertenece a los valores permitidos.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise ValueError(f"Falta la propiedad obligatoria {path}.{name}.")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"{path} contiene propiedades adicionales: {', '.join(extras)}.")
        for name, child in properties.items():
            if name in value:
                _validate_value(value[name], child, path=f"{path}.{name}")
    elif isinstance(value, list):
        _validate_length(value, schema, path, "Items")
        items = schema.get("items")
        if items is not None:
            for index, item in enumerate(value):
                _validate_value(item, items, path=f"{path}[{index}]")
    elif isinstance(value, str):
        _validate_length(value, schema, path, "Length")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ValueError(f"{path} no cumple el patrón requerido.")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ValueError(f"{path} es menor que el mínimo permitido.")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ValueError(f"{path} es mayor que el máximo permitido.")


def _matches_type(value: JsonValue, expected: object) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(expected), False)


def _validate_length(
    value: str | list[JsonValue],
    schema: dict[str, Any],
    path: str,
    suffix: str,
) -> None:
    minimum = schema.get(f"min{suffix}")
    maximum = schema.get(f"max{suffix}")
    if isinstance(minimum, int) and len(value) < minimum:
        raise ValueError(f"{path} es más corto que el mínimo permitido.")
    if isinstance(maximum, int) and len(value) > maximum:
        raise ValueError(f"{path} supera el máximo permitido.")
