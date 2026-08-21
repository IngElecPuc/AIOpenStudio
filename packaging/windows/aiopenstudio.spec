# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve().parents[1]
source_root = project_root / "src"
migrations = source_root / "aiopenstudio/infrastructure/database/migrations"
compliance_root = Path(os.environ["AIOPENSTUDIO_COMPLIANCE_DIR"]).resolve()

hidden_imports = [
    "PIL.Image",
    "psycopg",
    "sounddevice",
]
hidden_imports += collect_submodules("keyring.backends")

analysis = Analysis(
    [str(project_root / "packaging/windows/entrypoint.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        (str(migrations), "aiopenstudio/infrastructure/database/migrations"),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "docs/user-guide.md"), "."),
        (str(project_root / "docs/troubleshooting.md"), "."),
        (str(compliance_root / "THIRD_PARTY_NOTICES.txt"), "."),
        (str(compliance_root / "dependency-inventory.json"), "."),
        (str(compliance_root / "licenses"), "licenses"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "torchvision", "torchaudio", "transformers"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="AIOpenStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AIOpenStudio",
)
