from __future__ import annotations

import argparse

from scripts.validate_whisper_vertical import (
    _scenario_options,
    _scenario_passed,
)

from aiopenstudio.core.contracts import (
    ModelId,
    TranscriptionResult,
    TranscriptionTask,
    VadMode,
)


def _arguments(scenario: str, *, intervals: list[str] | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        scenario=scenario,
        language="es",
        hotwords="AIOpenStudio",
        interval=intervals or [],
    )


def test_translation_scenario_has_spanish_input_and_english_only_task() -> None:
    options = _scenario_options(_arguments("translate"))

    assert options.source_language == "es"
    assert options.task is TranscriptionTask.TRANSLATE
    assert options.vad_mode is VadMode.AUTOMATIC


def test_interval_scenario_disables_vad_and_preserves_all_ranges() -> None:
    options = _scenario_options(_arguments("intervals", intervals=["0-10", "20-25.5"]))

    assert options.vad_mode is VadMode.DISABLED
    assert [(item.start_seconds, item.end_seconds) for item in options.intervals] == [
        (0.0, 10.0),
        (20.0, 25.5),
    ]


def test_translation_report_requires_english_output() -> None:
    result = TranscriptionResult(
        operation_id="translation",
        model=ModelId(runtime="faster-whisper", name="small"),
        source_path="audio.wav",
        output_language="en",
        task=TranscriptionTask.TRANSLATE,
        elapsed_seconds=1,
    )

    assert _scenario_passed("translate", "completed", result, 0) is True
    assert (
        _scenario_passed(
            "translate",
            "completed",
            result.model_copy(update={"output_language": "es"}),
            0,
        )
        is False
    )
