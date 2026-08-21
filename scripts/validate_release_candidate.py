"""Validate a Windows bundle, archive and update manifest as one release candidate."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from scripts.package_windows_distribution import sha256_file
    from scripts.verify_windows_distribution import verify_bundle
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from package_windows_distribution import sha256_file
    from verify_windows_distribution import verify_bundle

REQUIRED_BUNDLE_FILES = {
    "AIOpenStudio.exe",
    "LICENSE",
    "THIRD_PARTY_NOTICES.txt",
    "dependency-inventory.json",
    "user-guide.md",
    "troubleshooting.md",
}


@dataclass(frozen=True, slots=True)
class CandidateResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors

    def public_dict(self, *, archive_name: str, manifest_name: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "passed" if self.passed else "failed",
            "artifact": archive_name,
            "manifest": manifest_name,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_candidate(
    bundle: Path,
    archive: Path,
    manifest_path: Path,
    *,
    expected_version: str,
    require_signature: bool = False,
    forbidden_values: tuple[str, ...] = (),
) -> CandidateResult:
    errors = list(verify_bundle(bundle, forbidden_values))
    warnings: list[str] = []
    bundle_files = {
        path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()
    }
    for required in sorted(REQUIRED_BUNDLE_FILES - bundle_files):
        errors.append(f"archivo obligatorio ausente: {required}")
    _validate_dependency_inventory(bundle, bundle_files, errors)

    manifest: dict[str, object] = {}
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("el manifiesto no es un objeto JSON")
        manifest = loaded
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"manifiesto inválido: {error}")

    if archive.is_file() and manifest:
        _validate_manifest(
            archive,
            manifest,
            expected_version=expected_version,
            require_signature=require_signature,
            errors=errors,
            warnings=warnings,
        )
        _validate_archive(bundle_files, archive, errors)
    elif not archive.is_file():
        errors.append("artefacto ZIP ausente")

    return CandidateResult(tuple(errors), tuple(warnings))


def _validate_dependency_inventory(
    bundle: Path,
    bundle_files: set[str],
    errors: list[str],
) -> None:
    inventory_path = bundle / "dependency-inventory.json"
    if not inventory_path.is_file():
        return
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        dependencies = payload["dependencies"]
        if payload.get("schema_version") != 1 or not isinstance(dependencies, list):
            raise ValueError("esquema no soportado")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"inventario de dependencias inválido: {error}")
        return
    if not dependencies:
        errors.append("inventario de dependencias vacío")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            errors.append("entrada de dependencia inválida")
            continue
        name = str(dependency.get("name") or "dependencia sin nombre")
        if str(dependency.get("license") or "").strip().upper() == "UNKNOWN":
            errors.append(f"licencia sin resolver: {name}")
        license_files = dependency.get("license_files", [])
        if not isinstance(license_files, list):
            errors.append(f"lista de licencias inválida: {name}")
            continue
        for relative in license_files:
            path = PurePosixPath(str(relative))
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"ruta de licencia insegura: {name}")
            elif path.as_posix() not in bundle_files:
                errors.append(f"texto de licencia ausente: {name} ({path.as_posix()})")


def _validate_manifest(
    archive: Path,
    manifest: dict[str, object],
    *,
    expected_version: str,
    require_signature: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    expected = {
        "schema_version": 1,
        "update_contract_version": 1,
        "application_version": expected_version,
        "platform": "windows-x86_64",
        "artifact": archive.name,
        "size_bytes": archive.stat().st_size,
        "sha256": sha256_file(archive),
        "requires_application_shutdown": True,
        "preserves_user_data": True,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            errors.append(f"manifiesto inconsistente: {key}")
    signature = manifest.get("signature")
    if not signature:
        message = "artefacto sin firma de código registrada"
        (errors if require_signature else warnings).append(message)


def _validate_archive(bundle_files: set[str], archive: Path, errors: list[str]) -> None:
    try:
        with zipfile.ZipFile(archive) as packaged:
            names = {name for name in packaged.namelist() if not name.endswith("/")}
    except (OSError, zipfile.BadZipFile) as error:
        errors.append(f"ZIP inválido: {error}")
        return
    for name in sorted(names):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"ruta ZIP insegura: {name}")
    expected_names = {f"AIOpenStudio/{name}" for name in bundle_files}
    if names != expected_names:
        errors.append("el contenido ZIP no coincide con el bundle aprobado")


def write_report(
    result: CandidateResult,
    destination: Path,
    *,
    archive_name: str,
    manifest_name: str,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            result.public_dict(
                archive_name=archive_name,
                manifest_name=manifest_name,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument("--forbid", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = validate_candidate(
        args.bundle.resolve(),
        args.archive.resolve(),
        args.manifest.resolve(),
        expected_version=args.expected_version,
        require_signature=args.require_signature,
        forbidden_values=tuple(args.forbid),
    )
    report = write_report(
        result,
        args.report.resolve(),
        archive_name=args.archive.name,
        manifest_name=args.manifest.name,
    )
    for warning in result.warnings:
        print(f"[advertencia] {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"[rechazado] {error}", file=sys.stderr)
    print(f"Reporte: {report}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
