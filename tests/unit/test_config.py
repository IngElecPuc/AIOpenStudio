from pathlib import Path

import pytest
from pydantic import ValidationError

from aiopenstudio.core.config import AppSettings


def test_settings_do_not_create_configured_directories(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-data"

    settings = AppSettings(_env_file=None, data_dir=data_dir)

    assert settings.data_dir == data_dir
    assert not data_dir.exists()


def test_relative_paths_resolve_from_explicit_base(tmp_path: Path) -> None:
    resolved = AppSettings.resolve_path(Path("data/models"), tmp_path)

    assert resolved == (tmp_path / "data" / "models").resolve()


def test_model_library_children_resolve_from_shared_root(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        model_library_root=tmp_path,
        model_catalog_path=Path("catalog/models.sqlite3"),
    )

    assert settings.resolve_model_library_path(settings.model_catalog_path) == (
        tmp_path / "catalog/models.sqlite3"
    ).resolve()


def test_monitoring_soft_limits_must_be_below_hard_limits() -> None:
    with pytest.raises(ValidationError, match="límite blando de RAM"):
        AppSettings(monitoring_ram_soft_limit=0.95, monitoring_ram_hard_limit=0.90)

    with pytest.raises(ValidationError, match="límite blando de VRAM"):
        AppSettings(monitoring_vram_soft_limit=0.95, monitoring_vram_hard_limit=0.90)
