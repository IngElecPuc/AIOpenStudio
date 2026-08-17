from pathlib import Path

from aiopenstudio.core.config import AppSettings


def test_settings_do_not_create_configured_directories(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-data"

    settings = AppSettings(_env_file=None, data_dir=data_dir)

    assert settings.data_dir == data_dir
    assert not data_dir.exists()


def test_relative_paths_resolve_from_explicit_base(tmp_path: Path) -> None:
    resolved = AppSettings.resolve_path(Path("data/models"), tmp_path)

    assert resolved == (tmp_path / "data" / "models").resolve()
