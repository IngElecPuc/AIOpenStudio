from pathlib import Path

from aiopenstudio.infrastructure.paths import ApplicationPaths


def test_packaged_layout_resolves_relative_data_outside_binary_tree(tmp_path: Path) -> None:
    install_root = tmp_path / "Program Files" / "AIOpenStudio"
    user_root = tmp_path / "LocalAppData" / "AIOpenStudio"
    config_root = tmp_path / "AppData" / "AIOpenStudio"
    layout = ApplicationPaths(
        runtime_root=user_root,
        config_root=config_root,
        packaged=True,
    )

    resolved = layout.resolve_runtime(Path("data/runtime/memory.sqlite3"))

    assert resolved.is_relative_to(user_root)
    assert not resolved.is_relative_to(install_root)
    assert layout.env_file == config_root / ".env"


def test_explicit_absolute_path_is_preserved(tmp_path: Path) -> None:
    layout = ApplicationPaths(tmp_path / "runtime", tmp_path / "config", packaged=True)
    external = (tmp_path / "external-models").resolve()

    assert layout.resolve_runtime(external) == external
