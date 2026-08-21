import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiopenstudio.core.contracts import (
    DescribeContent,
    EnhancementStep,
    EnhanceOptions,
    ImageGenerationOptions,
    ImageGenerationRequest,
    ImageOperation,
    ImagePromptKind,
    ImagePromptReference,
    InpaintMode,
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


def test_transport_maps_advanced_inputs_by_discovered_component_identity(
    tmp_path: Path,
) -> None:
    components = [
        {"id": 0, "type": "state", "props": {"value": None}},
        {"id": 1, "type": "textbox", "props": {"elem_id": "positive_prompt"}},
        _component(2, "Negative Prompt"),
        _component(3, "Performance", "Speed"),
        _component(4, "Image Number", 1),
        {"id": 5, "type": "checkbox", "props": {"label": "Input Image", "value": False}},
        {"id": 6, "type": "textbox", "props": {"value": "uov"}},
        _component(7, "Upscale or Variation:", "Disabled"),
        {"id": 8, "type": "image", "props": {"label": "Image"}},
        _component(9, "Outpaint Direction", []),
        {"id": 10, "type": "image", "props": {"label": "Image", "elem_id": "inpaint_canvas"}},
        _component(11, "Inpaint Additional Prompt", ""),
        {"id": 12, "type": "image", "props": {"label": "Mask Upload"}},
        _component(13, "Disable initial latent in inpaint", False),
        _component(14, "Inpaint Engine", "v2.6"),
        _component(15, "Inpaint Denoising Strength", 1.0),
        _component(16, "Inpaint Respective Field", 0.618),
        _component(17, "Mixing Image Prompt and Inpaint", False),
        {"id": 18, "type": "image", "props": {"label": "Image"}},
        _component(19, "Stop At", 0.5),
        _component(20, "Weight", 0.6),
        {
            "id": 21,
            "type": "radio",
            "props": {
                "label": "Type",
                "value": "ImagePrompt",
                "choices": ["ImagePrompt", "PyraCanny", "CPDS", "FaceSwap"],
            },
        },
        {"id": 22, "type": "image", "props": {"label": "Image"}},
        _component(23, "Stop At", 0.5),
        _component(24, "Weight", 0.6),
        {
            "id": 25,
            "type": "radio",
            "props": {
                "label": "Type",
                "value": "ImagePrompt",
                "choices": ["ImagePrompt", "FaceSwap"],
            },
        },
        {
            "id": 26,
            "type": "image",
            "props": {"label": "Use with Enhance, skips image generation"},
        },
        _component(27, "Enhance", False),
        _component(28, "Upscale or Variation:", "Disabled"),
        _component(29, "Order of Processing", "Before First Enhancement"),
        _component(30, "Prompt", "Original Prompts"),
        _component(31, "Enable", False),
        _component(32, "Detection prompt", ""),
        _component(33, "Enhancement positive prompt", ""),
        _component(34, "Enhancement negative prompt", ""),
        _component(35, "Mask generation model", "sam"),
        _component(36, "SAM model", "vit_b"),
        _component(37, "Text Threshold", 0.25),
        _component(38, "Box Threshold", 0.3),
        _component(39, "Maximum number of detections", 0),
        _component(40, "Disable initial latent in inpaint", False),
        _component(41, "Inpaint Engine", "v2.6"),
        _component(42, "Inpaint Denoising Strength", 1.0),
        _component(43, "Inpaint Respective Field", 0.618),
        _component(44, "Mask Erode or Dilate", 0),
        _component(45, "Invert Mask", False),
        _component(46, "Gallery"),
    ]
    config = {
        "components": components,
        "dependencies": [
            {"inputs": list(range(47)), "outputs": []},
            {"inputs": [], "outputs": [46]},
        ],
    }
    source = tmp_path / "source.png"
    mask = tmp_path / "mask.png"
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    for index, path in enumerate((source, mask, first, second)):
        path.write_bytes(f"image-{index}".encode())
    request = ImageGenerationRequest(
        operation_id="advanced",
        model=ModelId(runtime="fooocus", name="model.safetensors"),
        prompt="replace",
        operation=ImageOperation.INPAINT,
        source_image=source,
        mask_image=mask,
        inpaint_mode=InpaintMode.MODIFY,
        inpaint_prompt="a window",
        references=(
            ImagePromptReference(path=first, kind=ImagePromptKind.PYRA_CANNY, weight=1),
            ImagePromptReference(path=second, kind=ImagePromptKind.FACE_SWAP, stop_at=0.9),
        ),
        mix_references=True,
    )

    arguments = GradioFooocusTransport._generation_arguments(config, 0, request)
    mapped = {
        component_id: value for component_id, value in zip(range(1, 47), arguments, strict=True)
    }

    assert mapped[5] is True
    assert mapped[6] == "inpaint"
    assert mapped[10] == GradioFooocusTransport._image_payload(source)
    assert mapped[11] == "a window"
    assert mapped[12] == GradioFooocusTransport._image_payload(mask)
    assert mapped[13] is True
    assert mapped[16] == 0.0
    assert mapped[17] is True
    assert (mapped[18], mapped[20], mapped[21]) == (
        GradioFooocusTransport._image_payload(first),
        1.0,
        "PyraCanny",
    )
    assert (mapped[22], mapped[23], mapped[25]) == (
        GradioFooocusTransport._image_payload(second),
        0.9,
        "FaceSwap",
    )


def test_transport_maps_enhance_step_and_discovers_capabilities(tmp_path: Path) -> None:
    config = {
        "components": [
            {"id": 0, "type": "textbox", "props": {"elem_id": "positive_prompt"}},
            _component(1, "Negative Prompt"),
            _component(2, "Performance", "Speed"),
            _component(3, "Image Number", 1),
            {"id": 4, "type": "checkbox", "props": {"label": "Input Image", "value": False}},
            {"id": 5, "type": "textbox", "props": {"value": "uov"}},
            _component(6, "Upscale or Variation:", "Disabled"),
            {"id": 7, "type": "image", "props": {"label": "Image"}},
            {"id": 8, "type": "image", "props": {"label": "Image"}},
            {
                "id": 9,
                "type": "radio",
                "props": {"label": "Type", "choices": ["ImagePrompt", "CPDS"]},
            },
            {
                "id": 10,
                "type": "image",
                "props": {"label": "Use with Enhance, skips image generation"},
            },
            _component(11, "Enhance", False),
            _component(12, "Upscale or Variation:", "Disabled"),
            _component(13, "Order of Processing", "Before First Enhancement"),
            _component(14, "Prompt", "Original Prompts"),
            _component(15, "Enable", False),
            _component(16, "Detection prompt", ""),
            _component(17, "Enhancement positive prompt", ""),
            _component(18, "Mask generation model", "sam"),
            _component(19, "Save Only Final Enhanced Image", False),
            _component(20, "Gallery"),
            _component(21, "Content Type", ["Photograph"]),
            _component(22, "Outpaint Direction", []),
        ],
        "dependencies": [
            {"inputs": list(range(20)), "outputs": []},
            {"inputs": [], "outputs": [20]},
        ],
    }
    source = tmp_path / "enhance.png"
    source.write_bytes(b"enhance-image")
    request = ImageGenerationRequest(
        operation_id="enhance",
        model=ModelId(runtime="fooocus", name="model.safetensors"),
        prompt="improve",
        operation=ImageOperation.ENHANCE,
        source_image=source,
        enhance=EnhanceOptions(
            uov_operation=ImageOperation.UPSCALE_2,
            steps=(EnhancementStep(detection_prompt="face", positive_prompt="details"),),
            save_only_final=True,
        ),
    )

    arguments = GradioFooocusTransport._generation_arguments(config, 0, request)
    mapped = dict(zip(range(20), arguments, strict=True))
    capabilities = GradioFooocusTransport._capabilities_from_config(config, "cached")

    assert mapped[10] == GradioFooocusTransport._image_payload(source)
    assert mapped[11] is True
    assert mapped[12] == "Upscale (2x)"
    assert mapped[15] is True
    assert mapped[16] == "face"
    assert mapped[17] == "details"
    assert mapped[19] is True
    assert capabilities.max_reference_images == 1
    assert capabilities.max_enhancement_steps == 1
    assert ImageOperation.ENHANCE in capabilities.operations
    assert ImageOperation.DESCRIBE in capabilities.operations
    assert ImagePromptKind.CPDS in capabilities.prompt_kinds


def test_transport_maps_describe_image_as_serialized_payload(tmp_path: Path) -> None:
    source = tmp_path / "describe.png"
    source.write_bytes(b"describe-image")
    config = {
        "components": [
            _component(0, "Content Type", ["Photograph"]),
            {"id": 1, "type": "image", "props": {"label": "Image"}},
            _component(2, "Apply Styles", True),
            _component(3, "Selected Styles", []),
        ],
        "dependencies": [{"inputs": [0, 1, 2], "outputs": [3]}],
    }
    request = ImageGenerationRequest(
        operation_id="describe",
        model=ModelId(runtime="fooocus", name="model.safetensors"),
        operation=ImageOperation.DESCRIBE,
        source_image=source,
        describe_content=(DescribeContent.PHOTOGRAPH, DescribeContent.ART_ANIME),
        describe_apply_styles=False,
    )

    index = GradioFooocusTransport._describe_index(config)
    arguments = GradioFooocusTransport._describe_arguments(config, index, request)

    assert arguments == (
        ["Photograph", "Art/Anime"],
        GradioFooocusTransport._image_payload(source),
        False,
    )
