"""Create a deterministic ZIP and update manifest from an approved onedir bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_reproducible_zip(source: Path, destination: Path, epoch: int) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    timestamp = datetime.fromtimestamp(max(epoch, 315532800), UTC)
    zip_timestamp = (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = Path("AIOpenStudio") / path.relative_to(source)
            info = zipfile.ZipInfo(relative.as_posix(), date_time=zip_timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    temporary.replace(destination)
    return destination


def write_update_manifest(archive: Path, destination: Path, version: str) -> Path:
    payload = {
        "schema_version": 1,
        "update_contract_version": 1,
        "application_version": version,
        "platform": "windows-x86_64",
        "artifact": archive.name,
        "size_bytes": archive.stat().st_size,
        "sha256": sha256_file(archive),
        "signature": None,
        "requires_application_shutdown": True,
        "preserves_user_data": True,
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=int(os.environ.get("SOURCE_DATE_EPOCH", "315532800")),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = args.output_dir.resolve()
    archive = create_reproducible_zip(
        args.bundle,
        output_dir / f"AIOpenStudio-{args.version}-windows-x86_64.zip",
        args.source_date_epoch,
    )
    manifest = write_update_manifest(
        archive,
        output_dir / f"AIOpenStudio-{args.version}-windows-x86_64.json",
        args.version,
    )
    print(f"Archivo: {archive}")
    print(f"Manifiesto: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
