import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiopenstudio.core.contracts import (
    ImageGenerationOptions,
    ImageGenerationRequest,
    ModelId,
)
from aiopenstudio.infrastructure.runtimes.fooocus import GradioFooocusTransport
from aiopenstudio.infrastructure.runtimes.fooocus import transport as transport_module


def _component(identifier: int, label: str, value: object = None) -> dict[str, object]:
    return {"id": identifier, "props": {"label": label, "value": value}}


def test_transport_discovers_indices_and_maps_request_by_component_labels() -> None:
    config = {
        "components": [
            {"id": 0, "type": "state", "props": {"value": None}},
            {
                "id": 1,
                "type": "textbox",
                "props": {"elem_id": "positive_prompt", "value": ""},
            },
            _component(2, "Negative Prompt"),
            _component(3, "Selected Styles", []),
            _component(4, "Performance", "Speed"),
            {
                "id": 5,
                "props": {
                    "label": "Aspect Ratios",
                    "value": "1024×1024 ∣ 1:1",
                    "choices": [["1152×896 ∣ 9:7", "1152×896 ∣ 9:7"]],
                },
            },
            _component(6, "Image Number", 1),
            _component(7, "Base Model", "default.safetensors"),
            _component(8, "Gallery"),
        ],
        "dependencies": [
            {"inputs": [0, 1, 2, 3, 4, 5, 6, 7], "outputs": []},
            {"inputs": [], "outputs": [8]},
        ],
    }
    request = ImageGenerationRequest(
        operation_id="run-1",
        model=ModelId(runtime="fooocus", name="local.safetensors"),
        prompt="a forest",
        negative_prompt="watermark",
        options=ImageGenerationOptions(
            width=1152,
            height=896,
            image_count=2,
            styles=("Fooocus V2", "Fooocus Photograph"),
        ),
    )

    prepare, generate = GradioFooocusTransport._generation_indices(config)
    arguments = GradioFooocusTransport._generation_arguments(config, prepare, request)

    assert (prepare, generate) == (0, 1)
    assert arguments == (
        "a forest",
        "watermark",
        ["Fooocus V2", "Fooocus Photograph"],
        "Speed",
        "1152×896 ∣ 9:7",
        2,
        "local.safetensors",
    )


def test_transport_extracts_only_supported_image_paths() -> None:
    paths = GradioFooocusTransport._extract_paths(
        [{"name": "one.png"}, ("two.webp", "ignore.txt"), {"path": "three.jpeg"}]
    )

    assert tuple(path.name for path in paths) == ("one.png", "two.webp", "three.jpeg")


def test_transport_persists_real_gradio_configuration(tmp_path: Path) -> None:
    config = {"components": [], "dependencies": [], "version": "3.41.2"}
    client = type("Client", (), {"config": config})()
    transport = GradioFooocusTransport(
        "http://127.0.0.1:7865",
        download_root=tmp_path / "gradio",
        client_factory=lambda _: client,
    )

    assert transport._make_client() is client
    assert json.loads((tmp_path / "gradio-config.json").read_text("utf-8")) == config


def test_transport_disables_broken_gradio_gallery_deserialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class Client:
        def __init__(self, source: str, **options: object) -> None:
            captured["source"] = source
            captured.update(options)
            self.config = {"components": [], "dependencies": []}

    monkeypatch.setattr(
        transport_module,
        "import_module",
        lambda _: SimpleNamespace(Client=Client),
    )
    transport = GradioFooocusTransport(
        "http://127.0.0.1:7865",
        download_root=tmp_path / "gradio",
    )

    transport._make_client()

    assert captured["source"] == "http://127.0.0.1:7865"
    assert captured["serialize"] is False
