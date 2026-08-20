from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError
from scripts.model_library import ENV_BEGIN, ENV_END, update_env_file

from aiopenstudio.core.model_library import (
    ArtifactKind,
    DownloadManifest,
    DownloadProvider,
    DownloadSpec,
    InstalledArtifact,
    ModelLibrarySettings,
)
from aiopenstudio.infrastructure.database.model_library_catalog import ModelLibraryCatalog


def test_library_paths_resolve_from_one_root(tmp_path: Path) -> None:
    settings = ModelLibrarySettings(_env_file=None, model_library_root=tmp_path)

    assert settings.catalog_file == (tmp_path / "catalog/model-library.sqlite3").resolve()
    assert settings.resolve(settings.whisper_models_dir) == (tmp_path / "whisper").resolve()


def test_download_spec_rejects_parent_traversal() -> None:
    with pytest.raises(ValidationError):
        DownloadSpec(
            artifact_id="speech.invalid",
            display_name="Invalid",
            kind=ArtifactKind.SPEECH,
            provider=DownloadProvider.HUGGINGFACE,
            source="owner/repository",
            source_url="https://example.invalid/repository",
            local_path=PurePosixPath("../outside"),
            family="invalid",
            license_name="MIT",
        )


def test_library_settings_reject_child_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ModelLibrarySettings(
            _env_file=None,
            model_library_root=tmp_path,
            model_catalog_path=Path("../outside.sqlite3"),
        )


def test_versioned_download_manifest_is_valid() -> None:
    manifest_path = Path(__file__).parents[2] / "models" / "download-catalog.json"

    manifest = DownloadManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    assert manifest.schema_version == 1
    assert len(manifest.artifacts) >= 20
    assert len({artifact.artifact_id for artifact in manifest.artifacts}) == len(
        manifest.artifacts
    )
    gemma4 = next(
        artifact
        for artifact in manifest.artifacts
        if artifact.artifact_id == "llm.gemma4-e4b-it-qat"
    )
    assert gemma4.runtime_reference == "gemma4:e4b-it-qat"
    assert gemma4.quantization == "QAT-Q6_K"
    assert gemma4.expected_size_bytes == 6_100_000_000
    fooocus_assets = {
        artifact.artifact_id: artifact
        for artifact in manifest.artifacts
        if artifact.family == "fooocus-runtime-asset"
    }
    assert set(fooocus_assets) == {
        "image.fooocus-xl-vae-approx",
        "image.fooocus-sd15-vae-approx",
        "image.fooocus-xl-to-v1-interposer",
        "image.fooocus-prompt-expansion",
    }
    assert fooocus_assets["image.fooocus-sd15-vae-approx"].local_path == PurePosixPath(
        "fooocus/vae_approx/vaeapp_sd15.pth"
    )
    assert fooocus_assets["image.fooocus-prompt-expansion"].local_path == PurePosixPath(
        "fooocus/prompt_expansion/fooocus_expansion/pytorch_model.bin"
    )


def test_shared_catalog_round_trip_and_events(tmp_path: Path) -> None:
    schema_path = Path(__file__).parents[2] / "schemas" / "model-library.sql"
    catalog = ModelLibraryCatalog(tmp_path / "catalog.sqlite3", schema_path)
    catalog.initialize()
    installed_at = datetime.now(UTC)
    artifact = InstalledArtifact(
        artifact_id="llm.example-7b-q4",
        display_name="Example 7B Q4",
        kind=ArtifactKind.LLM,
        provider=DownloadProvider.OLLAMA,
        family="example",
        variant="7b",
        quantization="Q4_K_M",
        source="example:7b-q4_K_M",
        source_url="https://example.invalid/model",
        revision="sha256:" + "a" * 64,
        runtime_reference="example:7b-q4_K_M",
        relative_path=PurePosixPath("ollama"),
        license_name="MIT",
        size_bytes=123,
        checksum_sha256="a" * 64,
        capabilities=("chat",),
        installed_at=installed_at,
        verified_at=installed_at,
    )

    catalog.save(artifact)
    catalog.record_event("run", artifact.artifact_id, "installed")

    loaded = catalog.get(artifact.artifact_id)
    assert loaded is not None
    assert loaded.relative_path == PurePosixPath("ollama")
    assert loaded.checksum_sha256 == "a" * 64
    assert catalog.latest_events()[artifact.artifact_id] == ("installed", None)


def test_env_update_replaces_legacy_managed_assignments(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AIOPENSTUDIO_LOG_LEVEL=DEBUG\n"
        "AIOPENSTUDIO_WEIGHTS_DIR=old/models\n"
        f"{ENV_BEGIN}\n"
        "AIOPENSTUDIO_MODEL_LIBRARY_ROOT=old/root\n"
        f"{ENV_END}\n",
        encoding="utf-8",
    )
    settings = ModelLibrarySettings(_env_file=None, model_library_root=tmp_path / "AIModels")

    update_env_file(env_file, settings)

    updated = env_file.read_text(encoding="utf-8")
    assert "AIOPENSTUDIO_LOG_LEVEL=DEBUG" in updated
    assert "old/models" not in updated
    assert "old/root" not in updated
    assert updated.count("AIOPENSTUDIO_WEIGHTS_DIR=") == 1
    assert updated.count(ENV_BEGIN) == 1
