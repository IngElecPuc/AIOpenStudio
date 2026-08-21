"""Generate deterministic dependency and license notices from installed metadata."""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

LICENSE_PREFIXES = ("license", "licence", "copying", "notice")
UNKNOWN_LICENSE = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    name: str
    version: str
    license: str
    homepage: str | None
    license_files: tuple[str, ...] = ()
    _sources: tuple[Path, ...] = field(default=(), repr=False, compare=False)

    def public_dict(self) -> dict[str, str | list[str] | None]:
        return {
            "name": self.name,
            "version": self.version,
            "license": self.license,
            "homepage": self.homepage,
            "license_files": list(self.license_files),
        }


def discover_dependencies(
    project: str,
    *,
    extras: tuple[str, ...] = (),
    includes: tuple[str, ...] = (),
) -> tuple[DependencyRecord, ...]:
    requested_extras: dict[str, set[str]] = {
        canonicalize_name(project): set(extras),
        **{canonicalize_name(name): set() for name in includes},
    }
    pending = list(requested_extras)
    processed_extras: dict[str, frozenset[str]] = {}
    distributions: dict[str, metadata.Distribution] = {}

    while pending:
        name = pending.pop(0)
        active_extras = frozenset(requested_extras[name])
        if processed_extras.get(name) == active_extras:
            continue
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError as error:
            raise RuntimeError(f"Dependencia instalada ausente: {name}") from error
        distributions[name] = distribution
        processed_extras[name] = active_extras
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if not _marker_applies(requirement, active_extras):
                continue
            dependency_name = canonicalize_name(requirement.name)
            dependency_extras = requested_extras.setdefault(dependency_name, set())
            previous = frozenset(dependency_extras)
            dependency_extras.update(requirement.extras)
            if dependency_name not in processed_extras or previous != dependency_extras:
                pending.append(dependency_name)

    return tuple(
        _record_for(distributions[name])
        for name in sorted(distributions)
        if name != canonicalize_name(project)
    )


def _marker_applies(requirement: Requirement, extras: frozenset[str]) -> bool:
    if requirement.marker is None:
        return True
    contexts = ("", *sorted(extras))
    return any(requirement.marker.evaluate({"extra": extra}) for extra in contexts)


def _record_for(distribution: metadata.Distribution) -> DependencyRecord:
    package_metadata = distribution.metadata
    name = package_metadata.get("Name") or "unknown-package"
    license_value = resolve_license(
        package_metadata.get("License-Expression"),
        package_metadata.get("License"),
        tuple(package_metadata.get_all("Classifier") or ()),
    )
    homepage = package_metadata.get("Home-page") or _project_homepage(
        tuple(package_metadata.get_all("Project-URL") or ())
    )
    sources = tuple(
        located
        for entry in distribution.files or ()
        if Path(entry).name.casefold().startswith(LICENSE_PREFIXES)
        if (located := distribution.locate_file(entry)).is_file()
        if located.stat().st_size <= 2 * 1024 * 1024
    )
    relative_names = _license_destinations(name, sources)
    return DependencyRecord(
        name=name,
        version=distribution.version,
        license=license_value,
        homepage=homepage,
        license_files=relative_names,
        _sources=sources,
    )


def resolve_license(
    expression: str | None,
    legacy: str | None,
    classifiers: tuple[str, ...],
) -> str:
    for value in (expression, legacy):
        if value and value.strip() and value.strip().upper() != UNKNOWN_LICENSE:
            return " ".join(value.split())
    approved = sorted(
        classifier.removeprefix("License :: OSI Approved :: ").strip()
        for classifier in classifiers
        if classifier.startswith("License :: OSI Approved :: ")
    )
    return "; ".join(approved) if approved else UNKNOWN_LICENSE


def _project_homepage(project_urls: tuple[str, ...]) -> str | None:
    for entry in project_urls:
        label, separator, url = entry.partition(",")
        if separator and label.strip().casefold() in {"homepage", "source", "repository"}:
            return url.strip()
    return None


def _license_destinations(package: str, sources: tuple[Path, ...]) -> tuple[str, ...]:
    package_dir = re.sub(r"[^A-Za-z0-9_.-]+", "-", canonicalize_name(package))
    used: set[str] = set()
    destinations: list[str] = []
    for index, source in enumerate(sources, start=1):
        basename = re.sub(r"[^A-Za-z0-9_.-]+", "-", source.name) or f"LICENSE-{index}"
        if basename.casefold() in used:
            basename = f"{index}-{basename}"
        used.add(basename.casefold())
        destinations.append((Path("licenses") / package_dir / basename).as_posix())
    return tuple(destinations)


def write_compliance_files(
    records: tuple[DependencyRecord, ...],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    licenses_dir = output_dir / "licenses"
    if licenses_dir.exists():
        shutil.rmtree(licenses_dir)
    for record in records:
        for source, relative in zip(record._sources, record.license_files, strict=True):
            destination = output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    inventory_path = output_dir / "dependency-inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependencies": [record.public_dict() for record in records],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    notices_path = output_dir / "THIRD_PARTY_NOTICES.txt"
    lines = [
        "AIOpenStudio third-party notices",
        "",
        "Generated from the installed distributions used for this build.",
        "Review the accompanying license files before redistribution.",
        "",
    ]
    for record in records:
        lines.append(f"{record.name} {record.version}")
        lines.append(f"License: {record.license}")
        if record.homepage:
            lines.append(f"Project: {record.homepage}")
        for license_file in record.license_files:
            lines.append(f"License file: {license_file}")
        lines.append("")
    notices_path.write_text("\n".join(lines), encoding="utf-8")
    return inventory_path, notices_path


def runtime_records() -> tuple[DependencyRecord, ...]:
    runtime_root = Path(sys.base_prefix)
    python_license = runtime_root / "LICENSE.txt"
    tk_license = runtime_root / "tcl/tk8.6/license.terms"
    missing = [path.name for path in (python_license, tk_license) if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Licencias del runtime ausentes: " + ", ".join(sorted(missing))
        )
    return (
        DependencyRecord(
            name="CPython",
            version=platform.python_version(),
            license="Python-2.0",
            homepage="https://www.python.org/",
            license_files=("licenses/cpython/LICENSE.txt",),
            _sources=(python_license,),
        ),
        DependencyRecord(
            name="Tcl-Tk",
            version="8.6",
            license="TCL",
            homepage="https://www.tcl-lang.org/",
            license_files=("licenses/tcl-tk/license.terms",),
            _sources=(tk_license,),
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="aiopenstudio")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--strict-licenses", action="store_true")
    parser.add_argument("--include-runtime-licenses", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        records = discover_dependencies(
            args.project,
            extras=tuple(args.extra),
            includes=tuple(args.include),
        )
        if args.include_runtime_licenses:
            records = tuple(sorted((*records, *runtime_records()), key=lambda item: item.name))
    except RuntimeError as error:
        print(f"[rechazado] {error}", file=sys.stderr)
        return 1
    unknown = tuple(record.name for record in records if record.license == UNKNOWN_LICENSE)
    if args.strict_licenses and unknown:
        print(
            "[rechazado] Licencias sin resolver: " + ", ".join(unknown),
            file=sys.stderr,
        )
        return 1
    inventory, notices = write_compliance_files(records, args.output_dir.resolve())
    print(f"Inventario: {inventory}")
    print(f"Avisos: {notices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
