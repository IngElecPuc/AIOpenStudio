"""Gradio transport isolated behind the Fooocus runtime contract."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import Event, RLock
from typing import Any, Protocol

import httpx

from aiopenstudio.core.contracts import (
    DescribeContent,
    EnhanceOrder,
    EnhancePromptSource,
    ImageGenerationCapabilities,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationRequest,
    ImageGenerationStage,
    ImageOperation,
    ImagePerformance,
    ImageProgress,
    ImagePromptKind,
)
from aiopenstudio.core.errors import RuntimeRequestError, RuntimeUnavailableError


class FooocusTransport(Protocol):
    def preflight(self) -> tuple[str, ...]: ...

    async def health(self) -> bool: ...

    async def list_models(self) -> Sequence[str]: ...

    async def list_styles(self) -> Sequence[str]: ...

    async def image_capabilities(self) -> ImageGenerationCapabilities: ...

    def generate(self, request: ImageGenerationRequest) -> AsyncIterator[ImageGenerationEvent]: ...

    async def cancel(self, operation_id: str) -> None: ...

    async def close(self) -> None: ...


class GradioFooocusTransport:
    """Discover Fooocus component IDs at runtime and submit one Gradio session."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout_seconds: float = 5.0,
        download_root: Path | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._client_factory = client_factory
        self._download_root = download_root
        self._active_jobs: dict[str, Any] = {}
        self._cancelled: dict[str, Event] = {}
        self._lock = RLock()

    def preflight(self) -> tuple[str, ...]:
        if self._client_factory is not None:
            return ()
        if importlib.util.find_spec("gradio_client") is None:
            return ("Falta la dependencia opcional gradio-client del entorno principal.",)
        try:
            installed = version("gradio-client")
        except PackageNotFoundError:
            return ("Falta la dependencia opcional gradio-client del entorno principal.",)
        if installed != "0.5.0":
            return (
                "Fooocus v2.5.5 requiere gradio-client 0.5.0 en el entorno principal; "
                f"se detectó {installed}.",
            )
        return ()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/config")
                return response.status_code == 200 and "components" in response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def list_models(self) -> Sequence[str]:
        config = await self._configuration()
        return self._choices(config, ("base model", "checkpoint"))

    async def list_styles(self) -> Sequence[str]:
        config = await self._configuration()
        return self._choices(config, ("selected styles", "styles"))

    async def image_capabilities(self) -> ImageGenerationCapabilities:
        source = "live"
        try:
            config = await self._configuration()
        except RuntimeUnavailableError:
            config = self._cached_configuration()
            source = "cached" if config else "unavailable"
        return self._capabilities_from_config(config, source)

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[ImageGenerationEvent | BaseException | None] = asyncio.Queue()
        cancelled = Event()
        self._cancelled[request.operation_id] = cancelled

        def emit(event: ImageGenerationEvent | BaseException | None) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        worker = asyncio.create_task(
            asyncio.to_thread(self._generate_blocking, request, cancelled, emit)
        )
        try:
            while True:
                item = await events.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await asyncio.gather(worker, return_exceptions=True)
            self._cancelled.pop(request.operation_id, None)
            self._active_jobs.pop(request.operation_id, None)

    async def cancel(self, operation_id: str) -> None:
        cancelled = self._cancelled.get(operation_id)
        if cancelled is not None:
            cancelled.set()
        job = self._active_jobs.get(operation_id)
        if job is not None:
            await asyncio.to_thread(job.cancel)

    async def close(self) -> None:
        for operation_id in tuple(self._cancelled):
            await self.cancel(operation_id)

    async def _configuration(self) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/config")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RuntimeUnavailableError(
                f"Fooocus no expone una configuración Gradio: {error}"
            ) from error
        if not isinstance(payload, Mapping):
            raise RuntimeUnavailableError("La configuración Gradio de Fooocus no es válida.")
        return payload

    def _cached_configuration(self) -> Mapping[str, Any]:
        if self._download_root is None:
            return {}
        path = self._download_root.parent / "gradio-config.json"
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def _generate_blocking(
        self,
        request: ImageGenerationRequest,
        cancelled: Event,
        emit: Callable[[ImageGenerationEvent | BaseException | None], None],
    ) -> None:
        try:
            client = self._make_client()
            config = getattr(client, "config", None)
            if not isinstance(config, Mapping):
                raise RuntimeUnavailableError("gradio_client no entregó el esquema de Fooocus.")
            if request.operation is ImageOperation.DESCRIBE:
                describe_index = self._describe_index(config)
                emit(
                    ImageGenerationEvent(
                        operation_id=request.operation_id,
                        kind=ImageGenerationEventKind.PROGRESS,
                        progress=ImageProgress(
                            stage=ImageGenerationStage.GENERATING,
                            detail="Fooocus está describiendo la imagen…",
                        ),
                    )
                )
                result = client.predict(
                    *self._describe_arguments(config, describe_index, request),
                    fn_index=describe_index,
                )
                description = self._extract_description(result)
                if not description:
                    raise RuntimeRequestError("Fooocus no devolvió una descripción.")
                emit(
                    ImageGenerationEvent(
                        operation_id=request.operation_id,
                        kind=ImageGenerationEventKind.DESCRIPTION,
                        description=description,
                    )
                )
                return
            prepare_index, generate_index = self._generation_indices(config)
            arguments = self._generation_arguments(config, prepare_index, request)
            emit(
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.PROGRESS,
                    progress=ImageProgress(
                        stage=ImageGenerationStage.LOADING,
                        detail="Aplicando parámetros y cargando el checkpoint…",
                    ),
                )
            )
            client.predict(*arguments, fn_index=prepare_index)
            if cancelled.is_set():
                emit(self._cancelled_event(request.operation_id))
                return
            emit(
                ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.PROGRESS,
                    progress=ImageProgress(
                        stage=ImageGenerationStage.GENERATING,
                        detail="Fooocus está generando…",
                    ),
                )
            )
            job = client.submit(fn_index=generate_index)
            with self._lock:
                self._active_jobs[request.operation_id] = job
            result = job.result()
            if cancelled.is_set():
                emit(self._cancelled_event(request.operation_id))
                return
            paths = self._extract_paths(result)
            if not paths:
                raise RuntimeRequestError("Fooocus terminó sin entregar rutas de imágenes.")
            for path in paths:
                emit(
                    ImageGenerationEvent(
                        operation_id=request.operation_id,
                        kind=ImageGenerationEventKind.IMAGE,
                        source_path=path,
                    )
                )
        except BaseException as error:
            emit(error)
        finally:
            emit(None)

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory(self._base_url)
        else:
            if importlib.util.find_spec("gradio_client") is None:
                raise RuntimeUnavailableError(
                    "El cliente de la API Fooocus requiere la dependencia opcional gradio_client."
                )
            client_type = import_module("gradio_client").Client
            if self._download_root is None:
                client = client_type(self._base_url, verbose=False)
            else:
                self._download_root.mkdir(parents=True, exist_ok=True)
                client = client_type(
                    self._base_url,
                    serialize=False,
                    output_dir=str(self._download_root),
                    verbose=False,
                )
        config = getattr(client, "config", None)
        if isinstance(config, Mapping) and self._download_root is not None:
            destination = self._download_root.parent / "gradio-config.json"
            temporary = destination.with_name(destination.name + ".partial")
            temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
            temporary.replace(destination)
        return client

    @staticmethod
    def _generation_indices(config: Mapping[str, Any]) -> tuple[int, int]:
        components = GradioFooocusTransport._component_map(config)
        dependencies = config.get("dependencies")
        if not isinstance(dependencies, list):
            raise RuntimeUnavailableError("Fooocus no publicó dependencias Gradio.")
        prepare_index: int | None = None
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, Mapping):
                continue
            labels = {
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("inputs", [])
            }
            if {"prompt", "negative prompt", "performance", "image number"} <= labels:
                prepare_index = index
                break
        if prepare_index is None:
            raise RuntimeUnavailableError(
                "El esquema Fooocus no contiene la operación de preparación esperada."
            )
        for index in range(prepare_index + 1, len(dependencies)):
            dependency = dependencies[index]
            if not isinstance(dependency, Mapping):
                continue
            output_labels = {
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("outputs", [])
            }
            if "gallery" in output_labels and len(dependency.get("inputs", [])) <= 1:
                return prepare_index, index
        if prepare_index + 1 < len(dependencies):
            return prepare_index, prepare_index + 1
        raise RuntimeUnavailableError("El esquema Fooocus no contiene la operación de generación.")

    @staticmethod
    def _generation_arguments(
        config: Mapping[str, Any],
        dependency_index: int,
        request: ImageGenerationRequest,
    ) -> tuple[Any, ...]:
        components = GradioFooocusTransport._component_map(config)
        dependencies = config.get("dependencies", [])
        dependency = dependencies[dependency_index]
        values: list[Any] = []
        label_counts: dict[str, int] = {}
        generic_image_index = 0
        active_reference_index: int | None = None
        enhance_started = False
        enhance_step_index = -1
        enabled_references = tuple(
            reference for reference in request.references if reference.enabled
        )
        for component_id in dependency.get("inputs", []):
            component = components.get(component_id, {})
            props = component.get("props", {}) if isinstance(component, Mapping) else {}
            if isinstance(component, Mapping) and component.get("type") == "state":
                # gradio-client inserts session State values before serialization.
                continue
            label = GradioFooocusTransport._label(component)
            occurrence = label_counts.get(label, 0)
            label_counts[label] = occurrence + 1
            value = props.get("value") if isinstance(props, Mapping) else None
            elem_id = str(props.get("elem_id") or "").casefold()
            component_type = str(component.get("type") or "").casefold()
            if label == "prompt" and not enhance_started:
                value = request.prompt
            elif label == "negative prompt":
                value = request.negative_prompt
            elif label in {"selected styles", "styles"}:
                value = list(request.options.styles)
            elif label == "performance":
                value = GradioFooocusTransport._performance(request.options.performance)
            elif label.startswith("aspect ratios"):
                requested = f"{request.options.width}×{request.options.height}"
                choices = props.get("choices", []) if isinstance(props, Mapping) else []
                values_by_choice = [
                    choice[1] if isinstance(choice, list) and len(choice) > 1 else choice
                    for choice in choices
                ]
                value = next(
                    (
                        candidate
                        for candidate in values_by_choice
                        if isinstance(candidate, str) and candidate.startswith(requested)
                    ),
                    requested,
                )
            elif label == "image number":
                value = request.options.image_count
            elif label == "output format":
                value = request.options.output_format
            elif label == "seed":
                value = str(request.options.seed if request.options.seed is not None else -1)
            elif label == "guidance scale":
                value = request.options.guidance_scale
            elif label == "image sharpness":
                value = request.options.sharpness
            elif label in {"base model (sdxl only)", "base model"}:
                value = request.model.name
            elif label == "input image":
                value = request.operation is not ImageOperation.TEXT_TO_IMAGE or bool(
                    enabled_references
                )
            elif (
                component_type == "textbox"
                and not label
                and value in {"uov", "ip", "inpaint", "enhance"}
            ):
                value = GradioFooocusTransport._input_tab(request.operation)
            elif label == "upscale or variation:":
                operation = (
                    request.operation
                    if occurrence == 0
                    else request.enhance.uov_operation
                    if request.enhance is not None
                    else None
                )
                value = GradioFooocusTransport._uov_method(operation)
            elif component_type == "image" and elem_id == "inpaint_canvas":
                value = (
                    GradioFooocusTransport._image_payload(request.source_image)
                    if request.operation in {ImageOperation.INPAINT, ImageOperation.OUTPAINT}
                    else None
                )
            elif label == "mask upload":
                value = GradioFooocusTransport._image_payload(request.mask_image)
            elif label == "image" and component_type == "image" and not elem_id:
                if generic_image_index == 0:
                    value = (
                        GradioFooocusTransport._image_payload(request.source_image)
                        if request.operation
                        in {
                            ImageOperation.VARY_SUBTLE,
                            ImageOperation.VARY_STRONG,
                            ImageOperation.UPSCALE_1_5,
                            ImageOperation.UPSCALE_2,
                            ImageOperation.UPSCALE_FAST_2,
                        }
                        else None
                    )
                    active_reference_index = None
                else:
                    active_reference_index = generic_image_index - 1
                    value = (
                        GradioFooocusTransport._image_payload(
                            enabled_references[active_reference_index].path
                        )
                        if active_reference_index < len(enabled_references)
                        else None
                    )
                generic_image_index += 1
            elif label == "stop at" and active_reference_index is not None:
                if active_reference_index < len(enabled_references):
                    value = enabled_references[active_reference_index].stop_at
            elif label == "weight" and active_reference_index is not None:
                if active_reference_index < len(enabled_references):
                    value = enabled_references[active_reference_index].weight
            elif label == "type" and active_reference_index is not None:
                if active_reference_index < len(enabled_references):
                    value = GradioFooocusTransport._prompt_kind(
                        enabled_references[active_reference_index].kind
                    )
            elif label == "outpaint direction":
                value = [direction.value.title() for direction in request.outpaint_directions]
            elif label == "inpaint additional prompt":
                value = request.inpaint_prompt
            elif label == "mixing image prompt and vary/upscale":
                value = request.mix_references and request.operation in {
                    ImageOperation.VARY_SUBTLE,
                    ImageOperation.VARY_STRONG,
                    ImageOperation.UPSCALE_1_5,
                    ImageOperation.UPSCALE_2,
                    ImageOperation.UPSCALE_FAST_2,
                }
            elif label == "mixing image prompt and inpaint":
                value = request.mix_references and request.operation in {
                    ImageOperation.INPAINT,
                    ImageOperation.OUTPAINT,
                }
            elif label == "disable initial latent in inpaint" and not enhance_started:
                value = request.inpaint_mode.value == "modify"
            elif label == "inpaint engine" and not enhance_started:
                value = "None" if request.inpaint_mode.value == "detail" else "v2.6"
            elif label == "inpaint denoising strength" and not enhance_started:
                value = 0.5 if request.inpaint_mode.value == "detail" else 1.0
            elif label == "inpaint respective field" and not enhance_started:
                value = 0.618 if request.inpaint_mode.value == "default" else 0.0
            elif label == "use with enhance, skips image generation":
                enhance_started = True
                active_reference_index = None
                value = (
                    GradioFooocusTransport._image_payload(request.source_image)
                    if request.operation is ImageOperation.ENHANCE
                    else None
                )
            elif label == "enhance":
                value = request.operation is ImageOperation.ENHANCE
            elif label == "order of processing" and request.enhance is not None:
                value = {
                    EnhanceOrder.BEFORE: "Before First Enhancement",
                    EnhanceOrder.AFTER: "After Last Enhancement",
                }[request.enhance.order]
            elif label == "prompt" and request.enhance is not None:
                value = {
                    EnhancePromptSource.ORIGINAL: "Original Prompts",
                    EnhancePromptSource.LAST_FILLED: "Last Filled Enhancement Prompts",
                }[request.enhance.prompt_source]
            elif label == "save only final enhanced image":
                value = bool(request.enhance and request.enhance.save_only_final)
            elif enhance_started and label == "enable":
                enhance_step_index += 1
                step = GradioFooocusTransport._enhance_step(request, enhance_step_index)
                value = step.enabled if step is not None else False
            elif enhance_started:
                step = GradioFooocusTransport._enhance_step(request, enhance_step_index)
                value = GradioFooocusTransport._enhance_value(label, value, step)
            elif label == "save metadata to images":
                value = False
            values.append(value)
        return tuple(values)

    @staticmethod
    def _input_tab(operation: ImageOperation) -> str:
        if operation in {
            ImageOperation.VARY_SUBTLE,
            ImageOperation.VARY_STRONG,
            ImageOperation.UPSCALE_1_5,
            ImageOperation.UPSCALE_2,
            ImageOperation.UPSCALE_FAST_2,
        }:
            return "uov"
        if operation in {ImageOperation.INPAINT, ImageOperation.OUTPAINT}:
            return "inpaint"
        if operation is ImageOperation.ENHANCE:
            return "enhance"
        return "ip"

    @staticmethod
    def _uov_method(operation: ImageOperation | None) -> str:
        if operation is None:
            return "Disabled"
        return {
            ImageOperation.VARY_SUBTLE: "Vary (Subtle)",
            ImageOperation.VARY_STRONG: "Vary (Strong)",
            ImageOperation.UPSCALE_1_5: "Upscale (1.5x)",
            ImageOperation.UPSCALE_2: "Upscale (2x)",
            ImageOperation.UPSCALE_FAST_2: "Upscale (Fast 2x)",
        }.get(operation, "Disabled")

    @staticmethod
    def _prompt_kind(kind: ImagePromptKind) -> str:
        return {
            ImagePromptKind.IMAGE_PROMPT: "ImagePrompt",
            ImagePromptKind.PYRA_CANNY: "PyraCanny",
            ImagePromptKind.CPDS: "CPDS",
            ImagePromptKind.FACE_SWAP: "FaceSwap",
        }[kind]

    @staticmethod
    def _image_payload(path: Path | None) -> str | None:
        """Encode a staged PNG because the client runs with serialization disabled."""
        if path is None:
            return None
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _enhance_step(request: ImageGenerationRequest, index: int) -> Any:
        if request.enhance is None or index < 0 or index >= len(request.enhance.steps):
            return None
        return request.enhance.steps[index]

    @staticmethod
    def _enhance_value(label: str, default: Any, step: Any) -> Any:
        if step is None:
            return default
        values = {
            "detection prompt": step.detection_prompt,
            "enhancement positive prompt": step.positive_prompt,
            "enhancement negative prompt": step.negative_prompt,
            "mask generation model": step.mask_model,
            "cloth category": step.cloth_category,
            "sam model": step.sam_model,
            "text threshold": step.text_threshold,
            "box threshold": step.box_threshold,
            "maximum number of detections": step.max_detections,
            "disable initial latent in inpaint": step.inpaint_mode.value == "modify",
            "inpaint engine": "None" if step.inpaint_mode.value == "detail" else "v2.6",
            "inpaint denoising strength": step.denoising_strength,
            "inpaint respective field": step.respective_field,
            "mask erode or dilate": step.mask_erode_or_dilate,
            "invert mask": step.invert_mask,
        }
        return values.get(label, default)

    @staticmethod
    def _describe_index(config: Mapping[str, Any]) -> int:
        components = GradioFooocusTransport._component_map(config)
        dependencies = config.get("dependencies", [])
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, Mapping):
                continue
            input_labels = [
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("inputs", [])
            ]
            output_labels = [
                GradioFooocusTransport._label(components.get(component_id))
                for component_id in dependency.get("outputs", [])
            ]
            if (
                "content type" in input_labels
                and "image" in input_labels
                and ("selected styles" in output_labels or "styles" in output_labels)
            ):
                return index
        raise RuntimeUnavailableError("El esquema Fooocus no expone la operación Describe.")

    @staticmethod
    def _describe_arguments(
        config: Mapping[str, Any],
        dependency_index: int,
        request: ImageGenerationRequest,
    ) -> tuple[Any, ...]:
        components = GradioFooocusTransport._component_map(config)
        dependency = config.get("dependencies", [])[dependency_index]
        values: list[Any] = []
        for component_id in dependency.get("inputs", []):
            component = components.get(component_id, {})
            props = component.get("props", {}) if isinstance(component, Mapping) else {}
            label = GradioFooocusTransport._label(component)
            value = props.get("value") if isinstance(props, Mapping) else None
            if label == "content type":
                value = [
                    {
                        DescribeContent.PHOTOGRAPH: "Photograph",
                        DescribeContent.ART_ANIME: "Art/Anime",
                    }[content]
                    for content in request.describe_content
                ]
            elif label == "image":
                value = GradioFooocusTransport._image_payload(request.source_image)
            elif label == "apply styles":
                value = request.describe_apply_styles
            values.append(value)
        return tuple(values)

    @staticmethod
    def _extract_description(value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (list, tuple)):
            return next(
                (item.strip() for item in value if isinstance(item, str) and item.strip()),
                None,
            )
        return None

    @staticmethod
    def _capabilities_from_config(
        config: Mapping[str, Any], source: str
    ) -> ImageGenerationCapabilities:
        if not config:
            return ImageGenerationCapabilities(schema_source="unavailable")
        components = GradioFooocusTransport._component_map(config)
        labels = {GradioFooocusTransport._label(component) for component in components.values()}
        operations = {ImageOperation.TEXT_TO_IMAGE}
        if "upscale or variation:" in labels:
            operations.update(
                {
                    ImageOperation.VARY_SUBTLE,
                    ImageOperation.VARY_STRONG,
                    ImageOperation.UPSCALE_1_5,
                    ImageOperation.UPSCALE_2,
                    ImageOperation.UPSCALE_FAST_2,
                }
            )
        if "inpaint or outpaint" in labels or "outpaint direction" in labels:
            operations.update({ImageOperation.INPAINT, ImageOperation.OUTPAINT})
        if "image prompt" in labels or any(
            "ImagePrompt" in GradioFooocusTransport._choice_values(component)
            for component in components.values()
        ):
            operations.add(ImageOperation.IMAGE_PROMPT)
        if "describe" in labels or "content type" in labels:
            operations.add(ImageOperation.DESCRIBE)
        if "enhance" in labels:
            operations.add(ImageOperation.ENHANCE)

        prompt_kinds: set[ImagePromptKind] = set()
        type_components = [
            component
            for component in components.values()
            if GradioFooocusTransport._label(component) == "type"
        ]
        type_choices = {
            choice
            for component in type_components
            for choice in GradioFooocusTransport._choice_values(component)
        }
        for kind, upstream in {
            ImagePromptKind.IMAGE_PROMPT: "ImagePrompt",
            ImagePromptKind.PYRA_CANNY: "PyraCanny",
            ImagePromptKind.CPDS: "CPDS",
            ImagePromptKind.FACE_SWAP: "FaceSwap",
        }.items():
            if upstream in type_choices:
                prompt_kinds.add(kind)

        try:
            prepare_index, _ = GradioFooocusTransport._generation_indices(config)
            input_components = [
                components.get(component_id, {})
                for component_id in config.get("dependencies", [])[prepare_index].get("inputs", [])
            ]
        except (RuntimeUnavailableError, IndexError, TypeError):
            input_components = []
        max_references = sum(
            1
            for component in input_components
            if GradioFooocusTransport._label(component) == "type"
        )
        enhance_seen = False
        max_enhancements = 0
        for component in input_components:
            label = GradioFooocusTransport._label(component)
            if label == "use with enhance, skips image generation":
                enhance_seen = True
            elif enhance_seen and label == "detection prompt":
                max_enhancements += 1
        return ImageGenerationCapabilities(
            operations=frozenset(operations),
            prompt_kinds=frozenset(prompt_kinds),
            max_reference_images=max_references,
            max_enhancement_steps=max_enhancements,
            schema_source=source,
        )

    @staticmethod
    def _choice_values(component: Mapping[str, Any]) -> tuple[str, ...]:
        props = component.get("props", {})
        choices = props.get("choices", []) if isinstance(props, Mapping) else []
        return tuple(
            str(choice[1] if isinstance(choice, list) and len(choice) > 1 else choice)
            for choice in choices
        )

    @staticmethod
    def _component_map(config: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        components = config.get("components", [])
        return {
            int(component["id"]): component
            for component in components
            if isinstance(component, Mapping) and isinstance(component.get("id"), int)
        }

    @staticmethod
    def _label(component: Mapping[str, Any] | None) -> str:
        if not isinstance(component, Mapping):
            return ""
        props = component.get("props")
        if not isinstance(props, Mapping):
            return ""
        label = str(props.get("label") or "").strip().casefold()
        if label:
            return label
        elem_id = str(props.get("elem_id") or "").strip().casefold()
        return {
            "positive_prompt": "prompt",
            "negative_prompt": "negative prompt",
        }.get(elem_id, "")

    @staticmethod
    def _choices(config: Mapping[str, Any], labels: tuple[str, ...]) -> tuple[str, ...]:
        for component in GradioFooocusTransport._component_map(config).values():
            if not any(label in GradioFooocusTransport._label(component) for label in labels):
                continue
            props = component.get("props", {})
            choices = props.get("choices", []) if isinstance(props, Mapping) else []
            values = []
            for choice in choices:
                value = choice[1] if isinstance(choice, list) and len(choice) > 1 else choice
                if isinstance(value, str):
                    values.append(value)
            if values:
                return tuple(values)
        return ()

    @staticmethod
    def _performance(performance: ImagePerformance) -> str:
        return {
            ImagePerformance.SPEED: "Speed",
            ImagePerformance.QUALITY: "Quality",
            ImagePerformance.EXTREME_SPEED: "Extreme Speed",
        }[performance]

    @staticmethod
    def _extract_paths(value: Any) -> tuple[Path, ...]:
        found: list[Path] = []

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key in ("path", "name"):
                    candidate = item.get(key)
                    if isinstance(candidate, str):
                        visit(candidate)
                for nested in item.values():
                    if isinstance(nested, (list, tuple)):
                        visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
            elif isinstance(item, str):
                candidate = Path(item)
                if candidate.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
                    found.append(candidate)

        visit(value)
        return tuple(dict.fromkeys(found))

    @staticmethod
    def _cancelled_event(operation_id: str) -> ImageGenerationEvent:
        return ImageGenerationEvent(
            operation_id=operation_id,
            kind=ImageGenerationEventKind.CANCELLED,
            progress=ImageProgress(stage=ImageGenerationStage.CANCELLED),
        )
