import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aiopenstudio.core.contracts import (
    ChatImage,
    ChatInput,
    ChatMessage,
    ChatOptions,
    ComputeDevice,
    InferenceRequest,
    LoadPolicy,
    MessageRole,
    ModelId,
    RuntimeEventKind,
    StructuredOutputMode,
    StructuredOutputSpec,
    ThinkingCapability,
    UnloadTarget,
)
from aiopenstudio.core.errors import (
    ModelNotInstalledError,
    RuntimeUnavailableError,
    UnsupportedRuntimeOperationError,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime


class FakeOllamaClient:
    def __init__(self) -> None:
        self.models: list[dict[str, Any]] = [
            {
                "model": "phi4-mini:latest",
                "size": 2_400_000_000,
                "digest": "sha256:test",
                "modified_at": datetime(2026, 8, 17, tzinfo=UTC),
                "details": {
                    "family": "phi3",
                    "parameter_size": "3.8B",
                    "quantization_level": "Q4_K_M",
                },
            }
        ]
        self.running: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.show_calls: list[str] = []
        self.chat_started = asyncio.Event()
        self.block_chat = False
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def list(self) -> Any:
        return {"models": self.models}

    async def ps(self) -> Any:
        return {"models": self.running}

    async def show(self, model: str) -> Any:
        self.show_calls.append(model)
        return {
            "capabilities": ["completion", "vision", "thinking", "tools"],
            "parameters": (
                'temperature 0.2\ntop_p 0.8\ntop_k 20\nmin_p 0.05\n'
                'repeat_penalty 1.1\nnum_ctx 4096\nstop "<end>"'
            ),
            "model_info": {
                "phi3.context_length": 131_072,
                "phi3.mm.tokens_per_image": 256,
            },
        }

    async def generate(self, model: str, prompt: str = "", **kwargs: Any) -> Any:
        self.generate_calls.append({"model": model, "prompt": prompt, **kwargs})
        keep_alive = kwargs.get("keep_alive")
        if keep_alive == 0:
            self.running = []
        else:
            num_gpu = (kwargs.get("options") or {}).get("num_gpu")
            self.running = [
                {
                    "model": model,
                    "size": 2_400_000_000,
                    "size_vram": 0 if num_gpu == 0 else 2_000_000_000,
                    "expires_at": datetime.now(UTC) + timedelta(minutes=10),
                }
            ]
        return {"done": True}

    async def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> Any:
        self.chat_calls.append({"model": model, "messages": messages, **kwargs})

        async def chunks() -> AsyncIterator[dict[str, Any]]:
            self.chat_started.set()
            if self.block_chat:
                await asyncio.Event().wait()
            yield {"message": {"content": "Hola "}, "done": False}
            yield {
                "message": {"content": "mundo"},
                "done": True,
                "eval_count": 2,
                "eval_duration": 1_000_000,
            }

        return chunks()


class OfflineOllamaClient(FakeOllamaClient):
    async def list(self) -> Any:
        raise ConnectionError("offline")

    async def ps(self) -> Any:
        raise ConnectionError("offline")


class UninspectableOllamaClient(FakeOllamaClient):
    async def show(self, model: str) -> Any:
        raise ConnectionError(f"cannot inspect {model}")


def _request(operation_id: str = "operation-1") -> InferenceRequest:
    chat_input = ChatInput(messages=(ChatMessage(role=MessageRole.USER, content="Saluda"),))
    return InferenceRequest(
        operation_id=operation_id,
        model=ModelId(runtime="ollama", name="phi4-mini:latest"),
        inputs=chat_input.model_dump(mode="json"),
    )


def test_catalog_and_lifecycle_are_mapped_without_pulling() -> None:
    async def scenario() -> None:
        client = FakeOllamaClient()
        runtime = OllamaRuntime("http://test", client=client)
        model_id = ModelId(runtime="ollama", name="phi4-mini:latest")

        models = await runtime.list_models()
        loaded = await runtime.load(model_id, LoadPolicy())
        unloaded = await runtime.unload(model_id)

        assert models[0].metadata["quantization_level"] == "Q4_K_M"
        capabilities = models[0].metadata["chat_capabilities"]
        assert capabilities["supports_vision"] is True
        assert capabilities["thinking"] == ThinkingCapability.DECLARED.value
        assert capabilities["max_context_tokens"] == 131_072
        assert capabilities["estimated_tokens_per_image"] == 256
        assert capabilities["defaults"]["min_p"] == 0.05
        assert client.show_calls == ["phi4-mini:latest"]
        assert loaded.active_device is ComputeDevice.GPU
        assert loaded.vram_bytes == 2_000_000_000
        assert loaded.ram_bytes == 400_000_000
        assert not unloaded.loaded_in_ram
        assert not unloaded.loaded_in_gpu
        assert [call["keep_alive"] for call in client.generate_calls] == [600.0, 0]

    asyncio.run(scenario())


def test_chat_maps_neutral_settings_system_prompt_and_images(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = FakeOllamaClient()
        runtime = OllamaRuntime("http://test", client=client)
        prepared_image = tmp_path / "prepared.png"
        prepared_image.write_bytes(b"validated-image-payload")
        chat_input = ChatInput(
            messages=(
                ChatMessage(
                    role=MessageRole.USER,
                    content="Saluda",
                    images=(
                        ChatImage(
                            path=prepared_image,
                            mime_type="image/png",
                            sha256="a" * 64,
                            width=16,
                            height=16,
                        ),
                    ),
                ),
            ),
            system_prompt="Responde brevemente.",
            options=ChatOptions(min_p=0.1, repeat_penalty=1.2),
            output=StructuredOutputSpec(
                mode=StructuredOutputMode.JSON_SCHEMA,
                json_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            ),
        )
        request = InferenceRequest(
            operation_id="settings",
            model=ModelId(runtime="ollama", name="phi4-mini:latest"),
            inputs=chat_input.model_dump(mode="json"),
        )

        _ = [event async for event in runtime.run(request)]

        call = client.chat_calls[0]
        assert call["messages"][0] == {
            "role": "system",
            "content": "Responde brevemente.",
        }
        assert call["options"] == {"min_p": 0.1, "repeat_penalty": 1.2}
        assert call["format"] == {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }
        assert call["messages"][1]["images"] == [b"validated-image-payload"]

    asyncio.run(scenario())


def test_one_failed_capability_inspection_does_not_hide_installed_model() -> None:
    async def scenario() -> None:
        runtime = OllamaRuntime("http://test", client=UninspectableOllamaClient())

        models = await runtime.list_models()

        assert models[0].installed
        assert models[0].capabilities == frozenset({"chat", "text-generation"})
        assert "cannot inspect" in models[0].metadata["capability_inspection_error"]

    asyncio.run(scenario())


def test_device_selection_and_temporary_cpu_offload_are_explicit() -> None:
    async def scenario() -> None:
        client = FakeOllamaClient()
        runtime = OllamaRuntime("http://test", client=client)
        model_id = ModelId(runtime="ollama", name="phi4-mini:latest")
        loaded = await runtime.load(model_id, LoadPolicy(device=ComputeDevice.GPU))
        offloaded = await runtime.unload(model_id, UnloadTarget.DEVICE)

        assert loaded.loaded_in_gpu
        assert offloaded.loaded_in_ram
        assert not offloaded.loaded_in_gpu
        assert client.generate_calls[0]["options"] == {"num_gpu": -1}
        assert client.generate_calls[1]["options"] == {"num_gpu": 0}
        assert client.generate_calls[1]["keep_alive"] == -1

        with pytest.raises(UnsupportedRuntimeOperationError, match="liberar RAM"):
            await runtime.unload(model_id, UnloadTarget.RAM)

    asyncio.run(scenario())


def test_streaming_emits_text_metrics_and_completion() -> None:
    async def scenario() -> None:
        runtime = OllamaRuntime("http://test", client=FakeOllamaClient())
        events = [event async for event in runtime.run(_request())]

        assert [event.kind for event in events] == [
            RuntimeEventKind.STARTED,
            RuntimeEventKind.TEXT_DELTA,
            RuntimeEventKind.TEXT_DELTA,
            RuntimeEventKind.METRICS,
            RuntimeEventKind.COMPLETED,
        ]
        assert (
            "".join(
                str(event.payload["text"])
                for event in events
                if event.kind is RuntimeEventKind.TEXT_DELTA
            )
            == "Hola mundo"
        )

    asyncio.run(scenario())


def test_cancel_stops_an_active_stream() -> None:
    async def scenario() -> None:
        client = FakeOllamaClient()
        client.block_chat = True
        runtime = OllamaRuntime("http://test", client=client)
        events = []

        async def consume() -> None:
            events.extend([event async for event in runtime.run(_request("cancel-me"))])

        task = asyncio.create_task(consume())
        await client.chat_started.wait()
        await runtime.cancel("cancel-me")
        await task

        assert events[-1].kind is RuntimeEventKind.CANCELLED

    asyncio.run(scenario())


def test_missing_model_is_rejected_before_inference() -> None:
    async def scenario() -> None:
        client = FakeOllamaClient()
        client.models = []
        runtime = OllamaRuntime("http://test", client=client)

        with pytest.raises(ModelNotInstalledError, match="no se descargará"):
            _ = [event async for event in runtime.run(_request())]

    asyncio.run(scenario())


def test_offline_server_has_safe_health_and_catalog_error() -> None:
    async def scenario() -> None:
        runtime = OllamaRuntime("http://test", client=OfflineOllamaClient())

        assert (await runtime.health()).value == "unavailable"
        with pytest.raises(RuntimeUnavailableError, match="conectar"):
            await runtime.list_models()

    asyncio.run(scenario())
