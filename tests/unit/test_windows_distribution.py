import json
from pathlib import Path

from scripts.package_windows_distribution import (
    create_reproducible_zip,
    sha256_file,
    write_update_manifest,
)
from scripts.validate_release_candidate import validate_candidate
from scripts.verify_windows_distribution import verify_bundle

REQUIRED_CANDIDATE_FILES = (
    "AIOpenStudio.exe",
    "LICENSE",
    "THIRD_PARTY_NOTICES.txt",
    "dependency-inventory.json",
    "user-guide.md",
    "troubleshooting.md",
)


def test_distribution_verifier_rejects_local_state_and_private_paths(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "AIOpenStudio.exe").write_bytes(b"portable")
    assert verify_bundle(bundle) == ()

    (bundle / ".env").write_text("PASSWORD=private", encoding="utf-8")
    (bundle / "leak.bin").write_bytes(b"C:\\Users\\private-person\\repository")
    violations = verify_bundle(bundle, ("private-person",))

    assert any("estado local" in item for item in violations)
    assert any("privado" in item or "perfil Windows" in item for item in violations)


def test_reproducible_zip_and_update_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "AIOpenStudio.exe").write_bytes(b"executable")
    support = bundle / "_internal"
    support.mkdir()
    (support / "runtime.dll").write_bytes(b"runtime")

    first = create_reproducible_zip(bundle, tmp_path / "first.zip", 1_700_000_000)
    second = create_reproducible_zip(bundle, tmp_path / "second.zip", 1_700_000_000)

    assert sha256_file(first) == sha256_file(second)
    manifest_path = write_update_manifest(first, tmp_path / "manifest.json", "1.0.0")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["sha256"] == sha256_file(first)
    assert manifest["requires_application_shutdown"] is True
    assert manifest["preserves_user_data"] is True


def test_release_candidate_gate_matches_bundle_archive_and_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for filename in REQUIRED_CANDIDATE_FILES:
        (bundle / filename).write_text(filename, encoding="utf-8")
    (bundle / "dependency-inventory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependencies": [
                    {
                        "name": "Example",
                        "version": "1.0",
                        "license": "MIT",
                        "homepage": None,
                        "license_files": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    archive = create_reproducible_zip(bundle, tmp_path / "candidate.zip", 1_700_000_000)
    manifest = write_update_manifest(archive, tmp_path / "candidate.json", "1.2.3")

    result = validate_candidate(
        bundle,
        archive,
        manifest,
        expected_version="1.2.3",
    )

    assert result.passed
    assert result.errors == ()
    assert result.warnings == ("artefacto sin firma de código registrada",)

    archive.write_bytes(archive.read_bytes() + b"tampered")
    tampered = validate_candidate(
        bundle,
        archive,
        manifest,
        expected_version="1.2.3",
    )
    assert not tampered.passed
    assert any("manifiesto inconsistente" in error for error in tampered.errors)


def test_release_candidate_rejects_unknown_dependency_license(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for filename in REQUIRED_CANDIDATE_FILES:
        (bundle / filename).write_text(filename, encoding="utf-8")
    (bundle / "dependency-inventory.json").write_text(
        '{"schema_version": 1, "dependencies": '
        '[{"name": "Unsafe", "license": "UNKNOWN", "license_files": []}]}',
        encoding="utf-8",
    )
    archive = create_reproducible_zip(bundle, tmp_path / "candidate.zip", 1_700_000_000)
    manifest = write_update_manifest(archive, tmp_path / "candidate.json", "1.2.3")

    result = validate_candidate(bundle, archive, manifest, expected_version="1.2.3")

    assert not result.passed
    assert "licencia sin resolver: Unsafe" in result.errors
