"""Reject private paths, local configuration and runtime data from a Windows bundle."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

FORBIDDEN_FILENAMES = {
    ".env",
    "memory.sqlite3",
    "model-library.sqlite3",
    "postgres-profile.json",
    "download-checklist.md",
}
FORBIDDEN_ROOTS = {"data", "outputs", "cache", ".vscode", ".git"}
WINDOWS_HOME_PATTERN = re.compile(rb"(?i)[a-z]:\\users\\[^\\/\x00\r\n]+")


def verify_bundle(bundle: Path, forbidden_values: Iterable[str] = ()) -> tuple[str, ...]:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        return (f"No existe el bundle: {bundle}",)
    violations: list[str] = []
    tokens = tuple(
        token
        for value in forbidden_values
        if value.strip()
        for token in (value.encode("utf-8"), value.encode("utf-16-le"))
    )
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle)
        lowered_parts = tuple(part.casefold() for part in relative.parts)
        if lowered_parts and lowered_parts[0] in FORBIDDEN_ROOTS:
            violations.append(f"directorio local incluido: {relative}")
        if path.name.casefold() in FORBIDDEN_FILENAMES:
            violations.append(f"configuración/estado local incluido: {relative}")
        if not path.is_file():
            continue
        leak = _find_content_leak(path, tokens)
        if leak:
            violations.append(f"{leak}: {relative}")
    return tuple(violations)


def _find_content_leak(path: Path, tokens: tuple[bytes, ...]) -> str | None:
    carry = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                data = carry + chunk
                if any(token in data for token in tokens):
                    return "valor privado incrustado"
                collapsed = data.replace(b"\x00", b"")
                if WINDOWS_HOME_PATTERN.search(collapsed):
                    return "ruta de perfil Windows incrustada"
                carry = data[-4096:]
    except OSError as error:
        return f"no se pudo inspeccionar ({error})"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--forbid", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    violations = verify_bundle(args.bundle, args.forbid)
    if violations:
        for violation in violations:
            print(f"[rechazado] {violation}", file=sys.stderr)
        return 1
    print(f"Bundle aprobado: {args.bundle.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
