import pytest

from aiopenstudio.core.contracts import StructuredOutputMode, StructuredOutputSpec
from aiopenstudio.services.structured_output import validate_structured_response


def test_json_response_is_parsed_with_pydantic() -> None:
    result = validate_structured_response(
        '{"ok": true, "items": [1, 2]}',
        StructuredOutputSpec(mode=StructuredOutputMode.JSON),
    )

    assert result == {"ok": True, "items": [1, 2]}


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ValueError, match="no es JSON válido"):
        validate_structured_response(
            "respuesta libre",
            StructuredOutputSpec(mode=StructuredOutputMode.JSON),
        )


def test_documented_json_schema_subset_validates_nested_values() -> None:
    spec = StructuredOutputSpec(
        mode=StructuredOutputMode.JSON_SCHEMA,
        json_schema={
            "type": "object",
            "required": ["title", "scores"],
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "minLength": 2},
                "scores": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "integer", "minimum": 0, "maximum": 10},
                },
            },
        },
    )

    assert validate_structured_response('{"title":"AIO","scores":[8]}', spec)
    with pytest.raises(ValueError, match="propiedad obligatoria"):
        validate_structured_response('{"title":"AIO"}', spec)
    with pytest.raises(ValueError, match="máximo"):
        validate_structured_response('{"title":"AIO","scores":[12]}', spec)


def test_unsupported_schema_keywords_are_rejected_locally() -> None:
    spec = StructuredOutputSpec(
        mode=StructuredOutputMode.JSON_SCHEMA,
        json_schema={"type": "object", "oneOf": [{"type": "object"}]},
    )

    with pytest.raises(ValueError, match="no admitidas: oneOf"):
        validate_structured_response("{}", spec)
