"""Safe ingestion and session-scoped selection of external LLM context."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from aiopenstudio.core.contracts import (
    ChatImage,
    ContextInspection,
    ContextItemState,
    ContextKind,
    ContextSendPolicy,
    ContextStoragePolicy,
    Conversation,
    ConversationContextItem,
    ConversationMemory,
    ModelChatCapabilities,
    PreparedContext,
    PreparedContextBatch,
)
from aiopenstudio.core.errors import RuntimeRequestError

_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".md",
        ".py",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".js",
        ".ts",
        ".tsx",
        ".html",
        ".css",
        ".sql",
    }
)
_IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "BMP": "image/bmp"}
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp"})


class LLMContextService:
    """Validate selected files without executing them or persisting them implicitly."""

    def __init__(
        self,
        memory: ConversationMemory,
        *,
        snapshot_root: Path,
        prepared_root: Path,
        max_text_file_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_image_bytes: int = 32 * 1024 * 1024,
        max_image_pixels: int = 25_000_000,
        preview_characters: int = 4_000,
    ) -> None:
        self._memory = memory
        self._snapshot_root = snapshot_root.resolve()
        self._prepared_root = prepared_root.resolve()
        self._max_text_file_bytes = max_text_file_bytes
        self._max_total_bytes = max_total_bytes
        self._max_image_bytes = max_image_bytes
        self._max_image_pixels = max_image_pixels
        self._preview_characters = preview_characters
        self._session_items: dict[str, dict[str, ConversationContextItem]] = {}

    async def add(
        self,
        conversation_id: str,
        source_path: Path,
        *,
        send_policy: ContextSendPolicy = ContextSendPolicy.ONCE,
        enabled: bool = False,
        snapshot: bool = False,
    ) -> ConversationContextItem:
        return await asyncio.to_thread(
            self._add_blocking,
            conversation_id,
            source_path,
            send_policy,
            enabled,
            snapshot,
        )

    async def list_items(self, conversation_id: str) -> tuple[ConversationContextItem, ...]:
        return await asyncio.to_thread(self._list_items_blocking, conversation_id)

    async def inspect(self, item_id: str, conversation_id: str) -> ContextInspection:
        return await asyncio.to_thread(self._inspect_by_id_blocking, item_id, conversation_id)

    async def accept_changes(
        self,
        item_id: str,
        conversation_id: str,
    ) -> ConversationContextItem:
        return await asyncio.to_thread(
            self._accept_changes_blocking,
            item_id,
            conversation_id,
        )

    async def set_enabled(
        self,
        item_id: str,
        conversation_id: str,
        enabled: bool,
    ) -> ConversationContextItem:
        return await asyncio.to_thread(
            self._update_item_blocking,
            item_id,
            conversation_id,
            {"enabled": enabled},
        )

    async def set_send_policy(
        self,
        item_id: str,
        conversation_id: str,
        policy: ContextSendPolicy,
    ) -> ConversationContextItem:
        return await asyncio.to_thread(
            self._update_item_blocking,
            item_id,
            conversation_id,
            {"send_policy": policy},
        )

    async def reorder(self, conversation_id: str, ordered_ids: Sequence[str]) -> None:
        await asyncio.to_thread(self._reorder_blocking, conversation_id, tuple(ordered_ids))

    async def remove(self, item_id: str, conversation_id: str) -> bool:
        return await asyncio.to_thread(self._remove_blocking, item_id, conversation_id)

    async def purge_conversation(self, conversation_id: str) -> None:
        """Remove only app-owned snapshots/prepared copies before deleting memory rows."""
        await asyncio.to_thread(self._purge_conversation_blocking, conversation_id)

    async def set_remember_queue(self, conversation_id: str, enabled: bool) -> None:
        await asyncio.to_thread(self._set_remember_queue_blocking, conversation_id, enabled)

    async def prepare(
        self,
        conversation_id: str,
        capabilities: ModelChatCapabilities,
    ) -> PreparedContextBatch:
        return await asyncio.to_thread(
            self._prepare_blocking,
            conversation_id,
            capabilities,
        )

    async def consume_once(self, conversation_id: str, item_ids: Sequence[str]) -> None:
        await asyncio.to_thread(
            self._consume_once_blocking,
            conversation_id,
            tuple(item_ids),
        )

    def _add_blocking(
        self,
        conversation_id: str,
        source_path: Path,
        send_policy: ContextSendPolicy,
        enabled: bool,
        snapshot: bool,
    ) -> ConversationContextItem:
        conversation = self._require_conversation(conversation_id)
        source = source_path.expanduser().resolve()
        item_id = str(uuid4())
        item = self._build_item(
            item_id=item_id,
            conversation_id=conversation_id,
            source=source,
            order=len(self._items_for(conversation_id)),
            send_policy=send_policy,
            enabled=enabled,
            snapshot=snapshot,
        )
        self._session_items.setdefault(conversation_id, {})[item.id] = item
        if conversation.remember_context_queue:
            self._memory.save_context_item(item)
        return item

    def _build_item(
        self,
        *,
        item_id: str,
        conversation_id: str,
        source: Path,
        order: int,
        send_policy: ContextSendPolicy,
        enabled: bool,
        snapshot: bool,
        created_at: datetime | None = None,
    ) -> ConversationContextItem:
        if not source.is_file():
            raise RuntimeRequestError(f"No existe el archivo de contexto: {source.name}")
        suffix = source.suffix.casefold()
        if suffix in _TEXT_EXTENSIONS:
            kind = ContextKind.TEXT_FILE
            self._read_text(source)
        elif suffix in _IMAGE_EXTENSIONS:
            kind = ContextKind.IMAGE
            self._validate_image(source)
        else:
            raise RuntimeRequestError(
                f"El formato {suffix or '(sin extensión)'} no está permitido como contexto."
            )
        stat = source.stat()
        digest = self._sha256(source)
        snapshot_path: Path | None = None
        if snapshot:
            destination_dir = (self._snapshot_root / conversation_id / item_id).resolve()
            if not destination_dir.is_relative_to(self._snapshot_root):
                raise RuntimeRequestError("La ruta de snapshot no es segura.")
            destination_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = destination_dir / f"source{suffix}"
            temporary = snapshot_path.with_name(snapshot_path.name + ".partial")
            shutil.copy2(source, temporary)
            temporary.replace(snapshot_path)
            if self._sha256(snapshot_path) != digest:
                raise RuntimeRequestError("El snapshot no coincide con el archivo seleccionado.")
        return ConversationContextItem(
            id=item_id,
            conversation_id=conversation_id,
            kind=kind,
            source_path=source,
            display_name=source.name,
            order=order,
            enabled=enabled,
            send_policy=send_policy,
            storage_policy=(
                ContextStoragePolicy.SNAPSHOT
                if snapshot
                else ContextStoragePolicy.REFERENCE
            ),
            snapshot_path=snapshot_path,
            size_bytes=stat.st_size,
            sha256=digest,
            source_modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            created_at=created_at or datetime.now(UTC),
        )

    def _list_items_blocking(self, conversation_id: str) -> tuple[ConversationContextItem, ...]:
        self._require_conversation(conversation_id)
        return tuple(sorted(self._items_for(conversation_id).values(), key=lambda item: item.order))

    def _items_for(self, conversation_id: str) -> dict[str, ConversationContextItem]:
        session = self._session_items.setdefault(conversation_id, {})
        conversation = self._memory.get_conversation(conversation_id)
        if conversation is not None and conversation.remember_context_queue:
            for item in self._memory.list_context_items(conversation_id):
                session.setdefault(item.id, item)
        return session

    def _inspect_by_id_blocking(
        self,
        item_id: str,
        conversation_id: str,
    ) -> ContextInspection:
        item = self._require_item(item_id, conversation_id)
        return self._inspect_item(item)

    def _inspect_item(self, item: ConversationContextItem) -> ContextInspection:
        payload_path = self._payload_path(item)
        if payload_path is None or not payload_path.is_file():
            return ContextInspection(
                item=item,
                state=ContextItemState.MISSING,
                warning="El archivo ya no existe.",
            )
        try:
            stat = payload_path.stat()
            size = stat.st_size
            modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
            digest = self._sha256(payload_path)
            preview: str | None = None
            width: int | None = None
            height: int | None = None
            mime_type: str | None = None
            if item.kind is ContextKind.TEXT_FILE:
                preview = self._read_text(payload_path)[: self._preview_characters]
            else:
                actual_format, width, height = self._validate_image(payload_path)
                mime_type = _IMAGE_FORMATS[actual_format]
        except RuntimeRequestError as error:
            return ContextInspection(
                item=item,
                state=ContextItemState.INVALID,
                warning=str(error),
            )
        if digest != item.sha256 or size != item.size_bytes:
            return ContextInspection(
                item=item,
                state=ContextItemState.CHANGED,
                preview=preview,
                current_size_bytes=size,
                current_sha256=digest,
                current_modified_at=modified_at,
                width=width,
                height=height,
                mime_type=mime_type,
                warning="El archivo cambió desde que fue agregado.",
            )
        if (
            item.storage_policy is ContextStoragePolicy.REFERENCE
            and modified_at != item.source_modified_at
        ):
            return ContextInspection(
                item=item,
                state=ContextItemState.CHANGED,
                preview=preview,
                current_size_bytes=size,
                current_sha256=digest,
                current_modified_at=modified_at,
                width=width,
                height=height,
                mime_type=mime_type,
                warning="La fecha de modificación cambió desde que el archivo fue agregado.",
            )
        warning = None
        if item.storage_policy is ContextStoragePolicy.SNAPSHOT:
            if not item.source_path.is_file():
                warning = "El original ya no existe; se usará el snapshot consentido."
            elif (
                self._sha256(item.source_path) != item.sha256
                or datetime.fromtimestamp(item.source_path.stat().st_mtime, UTC)
                != item.source_modified_at
            ):
                warning = "El original cambió; se usará el snapshot consentido sin modificarlo."
        return ContextInspection(
            item=item,
            state=ContextItemState.READY,
            preview=preview,
            current_size_bytes=size,
            current_sha256=digest,
            current_modified_at=modified_at,
            width=width,
            height=height,
            mime_type=mime_type,
            warning=warning,
        )

    def _accept_changes_blocking(
        self,
        item_id: str,
        conversation_id: str,
    ) -> ConversationContextItem:
        current = self._require_item(item_id, conversation_id)
        refreshed = self._build_item(
            item_id=current.id,
            conversation_id=conversation_id,
            source=current.source_path,
            order=current.order,
            send_policy=current.send_policy,
            enabled=current.enabled,
            snapshot=current.storage_policy is ContextStoragePolicy.SNAPSHOT,
            created_at=current.created_at,
        )
        self._store_item(refreshed)
        return refreshed

    def _update_item_blocking(
        self,
        item_id: str,
        conversation_id: str,
        update: dict[str, object],
    ) -> ConversationContextItem:
        item = self._require_item(item_id, conversation_id).model_copy(update=update)
        self._store_item(item)
        return item

    def _reorder_blocking(self, conversation_id: str, ordered_ids: tuple[str, ...]) -> None:
        items = self._items_for(conversation_id)
        if len(set(ordered_ids)) != len(ordered_ids) or set(ordered_ids) != set(items):
            raise ValueError("El nuevo orden debe contener cada elemento exactamente una vez.")
        for order, item_id in enumerate(ordered_ids):
            self._store_item(items[item_id].model_copy(update={"order": order}))

    def _remove_blocking(self, item_id: str, conversation_id: str) -> bool:
        item = self._items_for(conversation_id).pop(item_id, None)
        deleted = self._memory.delete_context_item(item_id)
        if item is None:
            return deleted
        self._remove_owned_path(item.snapshot_path, self._snapshot_root)
        prepared = (self._prepared_root / conversation_id / f"{item.id}.png").resolve()
        self._remove_owned_path(prepared, self._prepared_root)
        return True

    def _purge_conversation_blocking(self, conversation_id: str) -> None:
        for item in tuple(self._items_for(conversation_id).values()):
            self._remove_blocking(item.id, conversation_id)
        self._session_items.pop(conversation_id, None)
        for root in (self._snapshot_root, self._prepared_root):
            directory = (root / conversation_id).resolve()
            if directory.is_relative_to(root) and directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    # A non-empty directory is preserved instead of broadening deletion.
                    pass

    def _set_remember_queue_blocking(self, conversation_id: str, enabled: bool) -> None:
        conversation = self._require_conversation(conversation_id)
        items = tuple(self._items_for(conversation_id).values())
        conversation.remember_context_queue = enabled
        conversation.updated_at = datetime.now(UTC)
        self._memory.save_conversation(conversation)
        if enabled:
            for item in items:
                self._memory.save_context_item(item)
        else:
            for item in self._memory.list_context_items(conversation_id):
                self._memory.delete_context_item(item.id)

    def _prepare_blocking(
        self,
        conversation_id: str,
        capabilities: ModelChatCapabilities,
    ) -> PreparedContextBatch:
        prepared: list[PreparedContext] = []
        total_bytes = 0
        image_count = 0
        for item in self._list_items_blocking(conversation_id):
            if not item.enabled:
                continue
            inspection = self._inspect_item(item)
            if inspection.state is not ContextItemState.READY:
                raise RuntimeRequestError(
                    f"El contexto {item.display_name} no está listo: {inspection.warning}"
                )
            total_bytes += item.size_bytes
            if total_bytes > self._max_total_bytes:
                raise RuntimeRequestError(
                    "El contexto habilitado supera el límite total configurado."
                )
            payload_path = self._payload_path(item)
            if payload_path is None:
                raise RuntimeRequestError(
                    f"No existe una fuente utilizable para {item.display_name}."
                )
            if item.kind is ContextKind.TEXT_FILE:
                content = self._read_text(payload_path)
                wrapped = self._wrap_untrusted(item, content)
                prepared.append(
                    PreparedContext(
                        item_id=item.id,
                        kind=item.kind,
                        display_name=item.display_name,
                        send_policy=item.send_policy,
                        sha256=item.sha256,
                        text=wrapped,
                        size_bytes=item.size_bytes,
                        estimated_tokens=self._estimate_tokens(wrapped),
                    )
                )
                continue
            if not capabilities.supports_vision:
                raise RuntimeRequestError(
                    f"El modelo seleccionado no declara visión; deshabilita {item.display_name}."
                )
            image_count += 1
            maximum = capabilities.max_images_per_message or 1
            if image_count > maximum:
                raise RuntimeRequestError(
                    f"El modelo permite como máximo {maximum} imagen(es) validadas por turno."
                )
            chat_image = self._normalize_image(item, payload_path)
            prepared.append(
                PreparedContext(
                    item_id=item.id,
                    kind=item.kind,
                    display_name=item.display_name,
                    send_policy=item.send_policy,
                    sha256=item.sha256,
                    image=chat_image,
                    size_bytes=item.size_bytes,
                    estimated_tokens=capabilities.estimated_tokens_per_image or 1_024,
                )
            )
        return PreparedContextBatch(
            items=tuple(prepared),
            total_bytes=total_bytes,
            estimated_tokens=sum(item.estimated_tokens for item in prepared),
        )

    def _consume_once_blocking(
        self,
        conversation_id: str,
        item_ids: tuple[str, ...],
    ) -> None:
        for item_id in item_ids:
            item = self._items_for(conversation_id).get(item_id)
            if item is not None and item.send_policy is ContextSendPolicy.ONCE:
                self._store_item(item.model_copy(update={"enabled": False}))

    def _payload_path(self, item: ConversationContextItem) -> Path | None:
        if item.storage_policy is ContextStoragePolicy.SNAPSHOT:
            return item.snapshot_path.resolve() if item.snapshot_path else None
        return item.source_path.resolve()

    def _normalize_image(self, item: ConversationContextItem, source: Path) -> ChatImage:
        destination_dir = (self._prepared_root / item.conversation_id).resolve()
        if not destination_dir.is_relative_to(self._prepared_root):
            raise RuntimeRequestError("La ruta preparada de contexto no es segura.")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{item.id}.png"
        temporary = destination.with_name(destination.name + ".partial")
        image_module = import_module("PIL.Image")
        image_ops = import_module("PIL.ImageOps")
        try:
            with image_module.open(source) as opened:
                transposed = image_ops.exif_transpose(opened)
                bands = transposed.getbands()
                normalized = transposed.convert(
                    "RGBA" if "A" in bands or "transparency" in transposed.info else "RGB"
                )
                width, height = normalized.size
                normalized.save(temporary, format="PNG")
                normalized.close()
            temporary.replace(destination)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise RuntimeRequestError(
                f"No fue posible normalizar {item.display_name}: {error}"
            ) from error
        return ChatImage(
            path=destination,
            mime_type="image/png",
            sha256=self._sha256(destination),
            width=width,
            height=height,
        )

    def _validate_image(self, source: Path) -> tuple[str, int, int]:
        if source.stat().st_size > self._max_image_bytes:
            raise RuntimeRequestError(f"La imagen {source.name} supera el límite configurado.")
        image_module = import_module("PIL.Image")
        try:
            with image_module.open(source) as opened:
                actual_format = str(opened.format or "").upper()
                frames = int(getattr(opened, "n_frames", 1))
                width, height = opened.size
                opened.verify()
        except Exception as error:
            raise RuntimeRequestError(
                f"No fue posible validar la imagen {source.name}: {error}"
            ) from error
        if actual_format not in _IMAGE_FORMATS:
            raise RuntimeRequestError("El contenido real no es PNG, JPEG o BMP.")
        if frames != 1:
            raise RuntimeRequestError("No se admiten imágenes animadas o multipágina.")
        if width < 1 or height < 1 or width * height > self._max_image_pixels:
            raise RuntimeRequestError(f"La imagen {source.name} excede el límite de píxeles.")
        return actual_format, width, height

    def _read_text(self, source: Path) -> str:
        if source.stat().st_size > self._max_text_file_bytes:
            raise RuntimeRequestError(f"El archivo {source.name} supera el límite configurado.")
        payload = source.read_bytes()
        if b"\x00" in payload or self._looks_binary(payload):
            raise RuntimeRequestError(f"El archivo {source.name} parece binario.")
        try:
            return payload.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise RuntimeRequestError(
                f"El archivo {source.name} no es UTF-8 ni UTF-8 con BOM."
            ) from error

    @staticmethod
    def _looks_binary(payload: bytes) -> bool:
        if not payload:
            return False
        control = sum(byte < 32 and byte not in {9, 10, 13} for byte in payload)
        return control / len(payload) > 0.10

    @staticmethod
    def _wrap_untrusted(item: ConversationContextItem, content: str) -> str:
        marker = f"AIOPENSTUDIO_EXTERNAL_{item.id.replace('-', '_')}_{item.sha256[:12]}"
        while marker in content:
            marker += item.sha256[len(marker) % len(item.sha256)]
        return (
            f"<{marker} name={item.display_name!r} sha256={item.sha256}>\n"
            f"{content}\n"
            f"</{marker}>"
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max((len(text) + 3) // 4 + 8, 1)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _store_item(self, item: ConversationContextItem) -> None:
        self._session_items.setdefault(item.conversation_id, {})[item.id] = item
        conversation = self._require_conversation(item.conversation_id)
        if conversation.remember_context_queue:
            self._memory.save_context_item(item)

    def _require_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._memory.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeRequestError(f"La conversación {conversation_id!r} no existe.")
        return conversation

    def _require_item(self, item_id: str, conversation_id: str) -> ConversationContextItem:
        item = self._items_for(conversation_id).get(item_id)
        if item is None:
            raise RuntimeRequestError(f"El contexto {item_id!r} no existe en la conversación.")
        return item

    @staticmethod
    def _remove_owned_path(path: Path | None, root: Path) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            resolved.unlink()
