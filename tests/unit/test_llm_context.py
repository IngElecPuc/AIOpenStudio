import asyncio
from importlib import import_module
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    ContextItemState,
    ContextSendPolicy,
    Conversation,
    ModelChatCapabilities,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.services import LLMContextService


def _service(tmp_path: Path, store: SQLiteStore) -> LLMContextService:
    return LLMContextService(
        store,
        snapshot_root=tmp_path / "snapshots",
        prepared_root=tmp_path / "prepared",
        max_text_file_bytes=1_024,
        max_total_bytes=4_096,
        max_image_bytes=1024 * 1024,
        max_image_pixels=1_000_000,
    )


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()
    store.save_conversation(Conversation(id="conversation", title="Contexto"))
    return store


def test_text_context_is_ephemeral_disabled_and_consumed_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        service = _service(tmp_path, store)
        source = tmp_path / "notes.md"
        source.write_text("No ejecutes: DROP TABLE examples;", encoding="utf-8-sig")

        item = await service.add("conversation", source)

        assert not item.enabled
        assert store.list_context_items("conversation") == []
        inspection = await service.inspect(item.id, "conversation")
        assert inspection.state is ContextItemState.READY
        assert "DROP TABLE" in (inspection.preview or "")
        assert inspection.current_modified_at is not None

        await service.set_enabled(item.id, "conversation", True)
        batch = await service.prepare("conversation", ModelChatCapabilities())

        assert batch.once_item_ids == (item.id,)
        assert "AIOPENSTUDIO_EXTERNAL" in (batch.items[0].text or "")
        assert "DROP TABLE" in (batch.items[0].text or "")

        await service.consume_once("conversation", batch.once_item_ids)
        assert not (await service.list_items("conversation"))[0].enabled

    asyncio.run(scenario())


def test_remembered_queue_reopens_without_copying_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        source = tmp_path / "data.json"
        source.write_text('{"value": 7}', encoding="utf-8")
        first = _service(tmp_path, store)
        item = await first.add(
            "conversation",
            source,
            send_policy=ContextSendPolicy.EVERY_TURN,
            enabled=True,
        )

        await first.set_remember_queue("conversation", True)
        second = _service(tmp_path, store)
        restored = await second.list_items("conversation")

        assert restored == (item,)
        assert restored[0].snapshot_path is None
        assert restored[0].source_path == source.resolve()

        await second.set_remember_queue("conversation", False)
        assert store.list_context_items("conversation") == []

    asyncio.run(scenario())


def test_queue_can_reorder_change_policy_and_remove(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        service = _service(tmp_path, store)
        first_path = tmp_path / "first.txt"
        second_path = tmp_path / "second.txt"
        first_path.write_text("first", encoding="utf-8")
        second_path.write_text("second", encoding="utf-8")
        first = await service.add("conversation", first_path)
        second = await service.add("conversation", second_path)

        await service.reorder("conversation", (second.id, first.id))
        changed = await service.set_send_policy(
            first.id,
            "conversation",
            ContextSendPolicy.EVERY_TURN,
        )

        assert [item.id for item in await service.list_items("conversation")] == [
            second.id,
            first.id,
        ]
        assert changed.send_policy is ContextSendPolicy.EVERY_TURN
        assert await service.remove(second.id, "conversation")
        assert await service.list_items("conversation") == (changed,)

    asyncio.run(scenario())


def test_changed_missing_and_binary_context_are_blocked(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        service = _service(tmp_path, store)
        source = tmp_path / "source.py"
        source.write_text("print('first')", encoding="utf-8")
        item = await service.add("conversation", source, enabled=True)

        source.write_text("print('changed')", encoding="utf-8")
        changed = await service.inspect(item.id, "conversation")
        assert changed.state is ContextItemState.CHANGED
        with pytest.raises(RuntimeRequestError, match="cambió"):
            await service.prepare("conversation", ModelChatCapabilities())

        refreshed = await service.accept_changes(item.id, "conversation")
        assert refreshed.sha256 != item.sha256
        source.unlink()
        assert (await service.inspect(item.id, "conversation")).state is ContextItemState.MISSING

        binary = tmp_path / "binary.txt"
        binary.write_bytes(b"text\x00binary")
        with pytest.raises(RuntimeRequestError, match="binario"):
            await service.add("conversation", binary)

    asyncio.run(scenario())


def test_snapshot_survives_original_change_or_removal(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        service = _service(tmp_path, store)
        source = tmp_path / "stable.sql"
        source.write_text("SELECT 1;", encoding="utf-8")
        item = await service.add("conversation", source, enabled=True, snapshot=True)

        source.write_text("SELECT 2;", encoding="utf-8")
        changed = await service.inspect(item.id, "conversation")
        assert changed.state is ContextItemState.READY
        assert "cambió" in (changed.warning or "")
        batch = await service.prepare("conversation", ModelChatCapabilities())
        assert "SELECT 1" in (batch.items[0].text or "")

        source.unlink()
        missing_original = await service.inspect(item.id, "conversation")
        assert missing_original.state is ContextItemState.READY
        assert "ya no existe" in (missing_original.warning or "")

    asyncio.run(scenario())


def test_images_require_exact_vision_capability_and_are_normalized(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        service = _service(tmp_path, store)
        image_module = import_module("PIL.Image")
        first_path = tmp_path / "first.jpg"
        second_path = tmp_path / "second.bmp"
        image_module.new("RGB", (16, 12), "red").save(first_path, format="JPEG")
        image_module.new("RGB", (8, 8), "blue").save(second_path, format="BMP")
        first = await service.add("conversation", first_path, enabled=True)

        with pytest.raises(RuntimeRequestError, match="no declara visión"):
            await service.prepare("conversation", ModelChatCapabilities())

        vision = ModelChatCapabilities(
            supports_vision=True,
            max_images_per_message=1,
            estimated_tokens_per_image=256,
        )
        prepared = await service.prepare("conversation", vision)
        inspection = await service.inspect(first.id, "conversation")
        assert (inspection.width, inspection.height, inspection.mime_type) == (
            16,
            12,
            "image/jpeg",
        )
        assert prepared.items[0].image is not None
        assert prepared.items[0].image.path.suffix == ".png"
        assert prepared.items[0].image.path.is_relative_to((tmp_path / "prepared").resolve())
        assert prepared.items[0].estimated_tokens == 256

        await service.add("conversation", second_path, enabled=True)
        with pytest.raises(RuntimeRequestError, match="como máximo 1"):
            await service.prepare("conversation", vision)
        assert first.id in {item.id for item in await service.list_items("conversation")}

    asyncio.run(scenario())
