import pytest
from pydantic import ValidationError

from aiopenstudio.core.contracts import ContextOverflowPolicy, StructuredOutputMode
from aiopenstudio.ui.llm_settings import (
    OUTPUT_SCHEMA,
    OUTPUT_TEXT,
    OVERFLOW_TRUNCATE,
    THINK_OFF,
    parse_generation_selection,
)
from aiopenstudio.ui.llm_transcript import markdown_blocks


def test_blank_generation_fields_restore_model_defaults() -> None:
    selection = parse_generation_selection(
        {},
        system_prompt="  ",
        stop_sequences="",
        thinking=THINK_OFF,
        overflow=OVERFLOW_TRUNCATE,
        output_mode=OUTPUT_TEXT,
        json_schema="",
    )

    assert selection.options.runtime_options() == {}
    assert selection.system_prompt is None
    assert selection.think is False
    assert selection.overflow_policy is ContextOverflowPolicy.TRUNCATE_OLDEST


def test_generation_values_and_schema_are_validated() -> None:
    selection = parse_generation_selection(
        {"temperature": "0,3", "top_k": "20", "max_new_tokens": "512"},
        system_prompt="Responde con datos.",
        stop_sequences="END\nSTOP\n",
        thinking=THINK_OFF,
        overflow="Rechazar",
        output_mode=OUTPUT_SCHEMA,
        json_schema='{"type":"object","properties":{"answer":{"type":"string"}}}',
    )

    assert selection.options.temperature == 0.3
    assert selection.options.stop == ("END", "STOP")
    assert selection.output.mode is StructuredOutputMode.JSON_SCHEMA

    with pytest.raises(ValidationError):
        parse_generation_selection(
            {"temperature": "5"},
            system_prompt="",
            stop_sequences="",
            thinking=THINK_OFF,
            overflow="Rechazar",
            output_mode=OUTPUT_TEXT,
            json_schema="",
        )


def test_markdown_renderer_keeps_links_inert_and_code_copyable() -> None:
    blocks = markdown_blocks(
        "# Título\n\n[documentación](https://example.test)\n\n```py\nprint('ok')\n```"
    )

    assert blocks[0].kind == "heading1"
    assert blocks[1].text == "documentación ⟨https://example.test⟩"
    assert blocks[2].kind == "code"
    assert blocks[2].label == "py"
    assert blocks[2].text == "print('ok')"


def test_plain_text_renderer_does_not_interpret_markdown() -> None:
    assert markdown_blocks("[x](https://example.test)", plain_text=True)[0].text == (
        "[x](https://example.test)"
    )
