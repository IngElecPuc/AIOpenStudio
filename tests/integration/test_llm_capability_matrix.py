import asyncio
import hashlib
import os
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    ChatImage,
    ChatInput,
    ChatMessage,
    InferenceRequest,
    MessageRole,
    ModelId,
    RuntimeEventKind,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime

RUN_MATRIX = os.getenv("AIOPENSTUDIO_RUN_LLM_MATRIX") == "1"


@pytest.mark.ollama_integration
@pytest.mark.skipif(not RUN_MATRIX, reason="LLM capability matrix is opt-in")
def test_installed_text_vision_and_thinking_tags() -> None:
    """Run only against exact, user-selected installed tags; never pull models."""

    async def scenario() -> None:
        text_tag = _required_environment("AIOPENSTUDIO_LLM_TEXT_TAG")
        vision_tag = _required_environment("AIOPENSTUDIO_LLM_VISION_TAG")
        thinking_tag = _required_environment("AIOPENSTUDIO_LLM_THINKING_TAG")
        configured_image = Path(_required_environment("AIOPENSTUDIO_LLM_TEST_IMAGE"))
        image_path = await asyncio.to_thread(configured_image.resolve)
        assert await asyncio.to_thread(
            image_path.is_file
        ), "AIOPENSTUDIO_LLM_TEST_IMAGE no existe"
        runtime = OllamaRuntime(
            os.getenv("AIOPENSTUDIO_OLLAMA_BASE_URL", "http://localhost:11434")
        )
        try:
            models = {model.id.name: model for model in await runtime.list_models()}
            assert text_tag in models, f"Tag sólo-texto no instalado: {text_tag}"
            assert vision_tag in models, f"Tag visión no instalado: {vision_tag}"
            assert thinking_tag in models, f"Tag thinking no instalado: {thinking_tag}"
            assert models[vision_tag].metadata["chat_capabilities"]["supports_vision"]
            assert models[thinking_tag].metadata["chat_capabilities"]["thinking"] != "unavailable"

            text = await _run(runtime, text_tag, ChatInput(messages=(_message("Responde OK"),)))
            assert text.strip()
            image = ChatImage(
                path=image_path,
                mime_type=_mime_type(image_path),
                sha256=hashlib.sha256(
                    await asyncio.to_thread(image_path.read_bytes)
                ).hexdigest(),
                width=1,
                height=1,
            )
            vision = await _run(
                runtime,
                vision_tag,
                ChatInput(messages=(_message("Describe brevemente la imagen", (image,)),)),
            )
            assert vision.strip()
            direct = await _run(
                runtime,
                thinking_tag,
                ChatInput(messages=(_message("Responde sólo: 4"),), think=False),
            )
            assert direct.strip()
        finally:
            await runtime.close()

    asyncio.run(scenario())


def _message(prompt: str, images: tuple[ChatImage, ...] = ()) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=prompt, images=images)


async def _run(runtime: OllamaRuntime, tag: str, chat_input: ChatInput) -> str:
    request = InferenceRequest(
        operation_id=f"matrix-{tag}",
        model=ModelId(runtime="ollama", name=tag),
        inputs=chat_input.model_dump(mode="json"),
    )
    parts: list[str] = []
    terminal = None
    async for event in runtime.run(request):
        if event.kind is RuntimeEventKind.TEXT_DELTA:
            parts.append(str(event.payload.get("text", "")))
        if event.kind in {
            RuntimeEventKind.COMPLETED,
            RuntimeEventKind.CANCELLED,
            RuntimeEventKind.ERROR,
        }:
            terminal = event
    assert terminal is not None and terminal.kind is RuntimeEventKind.COMPLETED
    return "".join(parts)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    assert value, f"Falta configurar {name}"
    return value


def _mime_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    assert suffix in {".png", ".jpg", ".jpeg", ".bmp"}, "Formato de imagen no admitido"
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
    }[suffix]
