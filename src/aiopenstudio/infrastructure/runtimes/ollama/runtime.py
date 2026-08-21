"""Ollama implementation of the backend-neutral model runtime contract."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from ollama import AsyncClient, ResponseError

from aiopenstudio.core.contracts import (
    ChatInput,
    ChatOptions,
    ComputeDevice,
    InferenceRequest,
    LoadPolicy,
    ModelChatCapabilities,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeHealth,
    StructuredOutputMode,
    ThinkingCapability,
    UnloadTarget,
)
from aiopenstudio.core.errors import (
    ModelNotInstalledError,
    RuntimeRequestError,
    RuntimeUnavailableError,
    UnsupportedRuntimeOperationError,
)


class OllamaClient(Protocol):
    async def close(self) -> None: ...

    async def list(self) -> Any: ...

    async def ps(self) -> Any: ...

    async def show(self, model: str) -> Any: ...

    async def generate(self, model: str, prompt: str = "", **kwargs: Any) -> Any: ...

    async def chat(
        self, model: str, messages: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> Any: ...


class OllamaRuntime:
    """Use an already-running Ollama server without managing downloads or its process."""

    def __init__(self, base_url: str, client: OllamaClient | None = None) -> None:
        self._client = client or cast(OllamaClient, AsyncClient(host=base_url))
        self._operations: dict[str, asyncio.Task[Any]] = {}
        self._pinned_models: set[str] = set()
        self._model_details_cache: dict[str, tuple[str | None, dict[str, Any]]] = {}

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            manages_process=False,
            supports_device_selection=True,
            supports_partial_unload=True,
            supports_streaming=True,
            supports_cancellation=True,
        )

    async def close(self) -> None:
        await self._client.close()

    async def health(self) -> RuntimeHealth:
        try:
            await self._client.ps()
        except (ConnectionError, OSError):
            return RuntimeHealth.UNAVAILABLE
        except ResponseError:
            return RuntimeHealth.DEGRADED
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        health = await self.health()
        if health is RuntimeHealth.READY:
            return ProcessState.RUNNING
        if health is RuntimeHealth.UNAVAILABLE:
            return ProcessState.STOPPED
        return ProcessState.FAILED

    async def start(self) -> ProcessState:
        state = await self.process_state()
        if state is not ProcessState.RUNNING:
            raise RuntimeUnavailableError(
                "Ollama no responde. Inicia la aplicación o el servicio de Ollama."
            )
        return state

    async def stop(self) -> ProcessState:
        raise UnsupportedRuntimeOperationError(
            "AIOpenStudio no administra el proceso externo de Ollama."
        )

    async def list_models(self) -> Sequence[ModelDescriptor]:
        try:
            response = await self._client.list()
        except (ConnectionError, OSError) as error:
            raise RuntimeUnavailableError("No fue posible conectar con Ollama.") from error
        except ResponseError as error:
            raise RuntimeRequestError(str(error)) from error

        descriptors: list[ModelDescriptor] = []
        for model in _sequence_value(response, "models"):
            name = _string_value(model, "model")
            if not name:
                continue
            digest = _string_value(model, "digest")
            details = _value(model, "details")
            metadata: dict[str, Any] = {
                "digest": digest,
                "modified_at": _iso_value(_value(model, "modified_at")),
                "format": _string_value(details, "format"),
                "family": _string_value(details, "family"),
                "parameter_size": _string_value(details, "parameter_size"),
                "quantization_level": _string_value(details, "quantization_level"),
            }
            declared_capabilities: frozenset[str] = frozenset()
            try:
                inspected = await self._show_model(name, digest)
            except (ConnectionError, OSError, ResponseError) as error:
                metadata["capability_inspection_error"] = " ".join(str(error).split())[:300]
                capabilities = frozenset({"chat", "text-generation"})
            else:
                chat_capabilities = _chat_capabilities(inspected, digest)
                metadata["chat_capabilities"] = chat_capabilities.model_dump(mode="json")
                declared_capabilities = chat_capabilities.declared
                capabilities = frozenset(declared_capabilities)
                if "completion" in declared_capabilities:
                    capabilities = capabilities | {"chat", "text-generation"}
            descriptors.append(
                ModelDescriptor(
                    id=ModelId(runtime=self.name, name=name),
                    display_name=name,
                    capabilities=capabilities,
                    size_bytes=_int_value(model, "size"),
                    installed=True,
                    metadata={key: value for key, value in metadata.items() if value},
                )
            )
        return sorted(descriptors, key=lambda descriptor: descriptor.display_name.casefold())

    async def _show_model(self, name: str, digest: str | None) -> dict[str, Any]:
        cached = self._model_details_cache.get(name)
        if cached is not None and cached[0] == digest:
            return cached[1]
        response = await self._client.show(name)
        normalized = _mapping_value(response)
        self._model_details_cache[name] = (digest, normalized)
        return normalized

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        self._validate_model_id(model)
        await self._require_installed(model)
        keep_alive: float | str | None = policy.idle_timeout_seconds
        if policy.pin_in_ram or policy.pin_on_device:
            keep_alive = -1
            self._pinned_models.add(model.name)
        try:
            options: dict[str, int] | None = None
            if policy.device is ComputeDevice.CPU:
                options = {"num_gpu": 0}
            elif policy.device is ComputeDevice.GPU:
                options = {"num_gpu": -1}
            await self._client.generate(
                model=model.name,
                prompt="",
                stream=False,
                keep_alive=keep_alive,
                options=options,
            )
        except (ConnectionError, OSError) as error:
            raise RuntimeUnavailableError("Ollama dejó de responder durante la carga.") from error
        except ResponseError as error:
            raise RuntimeRequestError(str(error)) from error
        return await self.state(model)

    async def unload(
        self,
        model: ModelId,
        target: UnloadTarget = UnloadTarget.ALL,
    ) -> ModelState:
        self._validate_model_id(model)
        if target is UnloadTarget.RAM:
            raise UnsupportedRuntimeOperationError(
                "Ollama no puede liberar RAM y conservar exclusivamente la residencia GPU."
            )
        await self._require_installed(model)
        try:
            if target is UnloadTarget.DEVICE:
                await self._client.generate(
                    model=model.name,
                    prompt="",
                    stream=False,
                    keep_alive=-1,
                    options={"num_gpu": 0},
                )
            else:
                await self._client.generate(
                    model=model.name,
                    prompt="",
                    stream=False,
                    keep_alive=0,
                )
        except (ConnectionError, OSError) as error:
            raise RuntimeUnavailableError(
                "Ollama dejó de responder durante la descarga."
            ) from error
        except ResponseError as error:
            raise RuntimeRequestError(str(error)) from error
        if target is UnloadTarget.ALL:
            self._pinned_models.discard(model.name)
        return await self.state(model)

    async def state(self, model: ModelId) -> ModelState:
        self._validate_model_id(model)
        health = await self.health()
        process = await self.process_state()
        if health is not RuntimeHealth.READY:
            return ModelState(model=model, runtime_health=health, process_state=process)
        try:
            response = await self._client.ps()
        except (ConnectionError, OSError) as error:
            raise RuntimeUnavailableError(
                "No fue posible consultar los modelos activos."
            ) from error
        except ResponseError as error:
            raise RuntimeRequestError(str(error)) from error

        active = next(
            (
                candidate
                for candidate in _sequence_value(response, "models")
                if (_string_value(candidate, "model") or _string_value(candidate, "name"))
                == model.name
            ),
            None,
        )
        if active is None:
            return ModelState(
                model=model,
                runtime_health=health,
                process_state=process,
                detail="El modelo está instalado, pero no reside en memoria.",
            )

        total_bytes = _int_value(active, "size") or 0
        vram_bytes = _int_value(active, "size_vram") or 0
        ram_bytes = max(total_bytes - vram_bytes, 0)
        pinned = model.name in self._pinned_models
        return ModelState(
            model=model,
            runtime_health=health,
            process_state=process,
            ram_residency=ResidencyState.LOADED if ram_bytes > 0 else ResidencyState.UNLOADED,
            gpu_residency=ResidencyState.LOADED if vram_bytes > 0 else ResidencyState.UNLOADED,
            active_device=ComputeDevice.GPU if vram_bytes > 0 else ComputeDevice.CPU,
            ram_bytes=ram_bytes,
            vram_bytes=vram_bytes,
            pinned_in_ram=pinned and ram_bytes > 0,
            pinned_on_device=pinned and vram_bytes > 0,
            detail="Estado reportado por /api/ps de Ollama.",
        )

    async def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]:
        self._validate_model_id(request.model)
        chat_input = ChatInput.model_validate(request.inputs)
        await self._require_installed(request.model)
        task = asyncio.current_task()
        if task is None:
            raise RuntimeRequestError("No existe una tarea asíncrona para la operación.")
        if request.operation_id in self._operations:
            raise RuntimeRequestError(f"La operación {request.operation_id!r} ya está activa.")
        self._operations[request.operation_id] = task

        messages: list[dict[str, Any]] = []
        if chat_input.system_prompt:
            messages.append({"role": "system", "content": chat_input.system_prompt})
        for message in chat_input.messages:
            payload: dict[str, Any] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.images:
                try:
                    payload["images"] = [image.path.read_bytes() for image in message.images]
                except OSError as error:
                    raise RuntimeRequestError(
                        "No fue posible leer una imagen de contexto preparada."
                    ) from error
            messages.append(payload)
        try:
            yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.STARTED)
            chat_arguments: dict[str, Any] = {
                "stream": True,
                "think": chat_input.think,
                "options": chat_input.options.runtime_options(),
                "keep_alive": chat_input.keep_alive_seconds,
            }
            if chat_input.output.mode is StructuredOutputMode.JSON:
                chat_arguments["format"] = "json"
            elif chat_input.output.mode is StructuredOutputMode.JSON_SCHEMA:
                chat_arguments["format"] = chat_input.output.json_schema
            stream = await self._client.chat(
                model=request.model.name,
                messages=messages,
                **chat_arguments,
            )
            async for chunk in cast(AsyncIterator[Any], stream):
                response_message = _value(chunk, "message")
                text = _string_value(response_message, "content")
                thinking = _string_value(response_message, "thinking")
                if text:
                    yield RuntimeEvent(
                        operation_id=request.operation_id,
                        kind=RuntimeEventKind.TEXT_DELTA,
                        payload={"text": text},
                    )
                if thinking:
                    yield RuntimeEvent(
                        operation_id=request.operation_id,
                        kind=RuntimeEventKind.THINKING_DELTA,
                        payload={"text": thinking},
                    )
                if bool(_value(chunk, "done")):
                    metrics = _completion_metrics(chunk)
                    if metrics:
                        yield RuntimeEvent(
                            operation_id=request.operation_id,
                            kind=RuntimeEventKind.METRICS,
                            payload=metrics,
                        )
            yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.COMPLETED)
        except asyncio.CancelledError:
            yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.CANCELLED)
        except (ConnectionError, OSError, ResponseError) as error:
            yield RuntimeEvent(
                operation_id=request.operation_id,
                kind=RuntimeEventKind.ERROR,
                payload={"message": str(error), "error_type": type(error).__name__},
            )
        finally:
            self._operations.pop(request.operation_id, None)

    async def cancel(self, operation_id: str) -> None:
        task = self._operations.get(operation_id)
        if task is not None and not task.done():
            task.cancel()

    async def _require_installed(self, model: ModelId) -> None:
        installed = await self.list_models()
        if not any(candidate.id.name == model.name for candidate in installed):
            raise ModelNotInstalledError(
                f"El modelo {model.name!r} no está instalado en Ollama; "
                "no se descargará automáticamente."
            )

    def _validate_model_id(self, model: ModelId) -> None:
        if model.runtime != self.name:
            raise RuntimeRequestError(
                f"El runtime Ollama no puede operar el identificador {model.key!r}."
            )


def _value(source: object, key: str) -> object | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _mapping_value(source: object) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return {str(key): value for key, value in source.items()}
    model_dump = getattr(source, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return {str(key): value for key, value in dumped.items()}
    return {}


def _sequence_value(source: object, key: str) -> Sequence[object]:
    value = _value(source, key)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return cast(Sequence[object], value)
    return ()


def _string_value(source: object, key: str) -> str | None:
    value = _value(source, key)
    return value if isinstance(value, str) else None


def _int_value(source: object, key: str) -> int | None:
    value = _value(source, key)
    return int(value) if isinstance(value, (int, float)) else None


def _iso_value(value: object | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _completion_metrics(chunk: object) -> dict[str, Any]:
    fields = (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "done_reason",
    )
    return {field: value for field in fields if (value := _value(chunk, field)) is not None}


def _chat_capabilities(details: Mapping[str, Any], digest: str | None) -> ModelChatCapabilities:
    declared = frozenset(
        str(value).strip().casefold()
        for value in _sequence_value(details, "capabilities")
        if str(value).strip()
    )
    thinking = (
        ThinkingCapability.DECLARED
        if "thinking" in declared
        else ThinkingCapability.UNAVAILABLE
    )
    return ModelChatCapabilities(
        model_digest=digest,
        declared=declared,
        supports_vision="vision" in declared,
        thinking=thinking,
        supports_tools="tools" in declared,
        # Ollama's chat endpoint accepts ``format`` for installed completion models.
        # Model adherence is checked locally before marking the response complete.
        supports_structured_output="completion" in declared,
        max_context_tokens=_maximum_context_length(_value(details, "model_info")),
        max_images_per_message=1 if "vision" in declared else None,
        estimated_tokens_per_image=_tokens_per_image(_value(details, "model_info")),
        defaults=_parse_model_parameters(_string_value(details, "parameters") or ""),
    )


def _maximum_context_length(model_info: object | None) -> int | None:
    if not isinstance(model_info, Mapping):
        return None
    candidates = [
        int(value)
        for key, value in model_info.items()
        if str(key).endswith(".context_length")
        and isinstance(value, (int, float))
        and value > 0
    ]
    return max(candidates, default=None)


def _tokens_per_image(model_info: object | None) -> int | None:
    if not isinstance(model_info, Mapping):
        return None
    candidates = [
        int(value)
        for key, value in model_info.items()
        if str(key).endswith(".mm.tokens_per_image")
        and isinstance(value, (int, float))
        and value > 0
    ]
    return max(candidates, default=None)


def _parse_model_parameters(parameters: str) -> ChatOptions:
    values: dict[str, Any] = {}
    stops: list[str] = []
    integer_fields = {
        "top_k": "top_k",
        "seed": "seed",
        "num_ctx": "context_length",
        "num_predict": "max_new_tokens",
    }
    float_fields = {
        "temperature": "temperature",
        "top_p": "top_p",
        "min_p": "min_p",
        "repeat_penalty": "repeat_penalty",
    }
    for line in parameters.splitlines():
        name, separator, raw_value = line.strip().partition(" ")
        if not separator:
            continue
        value = raw_value.strip()
        if name == "stop":
            stops.append(value.strip('"'))
            continue
        try:
            if name in integer_fields:
                parsed: int | float = int(value)
                if name == "num_predict" and parsed < 1:
                    continue
                if name == "num_ctx" and parsed < 128:
                    continue
                values[integer_fields[name]] = parsed
            elif name in float_fields:
                values[float_fields[name]] = float(value)
        except ValueError:
            continue
    if stops:
        values["stop"] = tuple(stops)
    try:
        return ChatOptions.model_validate(values)
    except ValueError:
        return ChatOptions()
