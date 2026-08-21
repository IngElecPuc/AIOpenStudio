import json
from pathlib import Path

from scripts.generate_dependency_inventory import (
    UNKNOWN_LICENSE,
    DependencyRecord,
    resolve_license,
    runtime_records,
    write_compliance_files,
)


def test_license_resolution_prefers_expression_and_reports_unknown() -> None:
    assert resolve_license("MIT", "legacy", ()) == "MIT"
    assert (
        resolve_license(None, None, ("License :: OSI Approved :: Apache Software License",))
        == "Apache Software License"
    )
    assert resolve_license(None, None, ()) == UNKNOWN_LICENSE


def test_compliance_files_are_portable_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source-LICENSE"
    source.write_text("license text", encoding="utf-8")
    records = (
        DependencyRecord(
            name="Example",
            version="1.0",
            license="MIT",
            homepage="https://example.invalid/project",
            license_files=("licenses/example/LICENSE",),
            _sources=(source,),
        ),
    )

    inventory, notices = write_compliance_files(records, tmp_path / "compliance")
    first_inventory = inventory.read_bytes()
    first_notices = notices.read_bytes()
    write_compliance_files(records, tmp_path / "compliance")

    payload = json.loads(inventory.read_text(encoding="utf-8"))
    assert payload["dependencies"][0]["license_files"] == ["licenses/example/LICENSE"]
    assert str(tmp_path) not in inventory.read_text(encoding="utf-8")
    assert inventory.read_bytes() == first_inventory
    assert notices.read_bytes() == first_notices
    assert (tmp_path / "compliance/licenses/example/LICENSE").read_text() == "license text"


def test_runtime_inventory_includes_python_and_tcl_tk_license_texts() -> None:
    records = {record.name: record for record in runtime_records()}

    assert records["CPython"].license == "Python-2.0"
    assert records["Tcl-Tk"].license == "TCL"
    assert all(record._sources[0].is_file() for record in records.values())
