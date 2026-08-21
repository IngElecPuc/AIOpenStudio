"""Ignored local PostgreSQL profile and operating-system credential storage."""

from __future__ import annotations

import json
from pathlib import Path

from aiopenstudio.core.contracts import PostgresConnectionProfile


class PostgresProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PostgresConnectionProfile:
        if not self.path.is_file():
            return PostgresConnectionProfile()
        return PostgresConnectionProfile.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(self, profile: PostgresConnectionProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".partial")
        temporary.write_text(
            json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class KeyringCredentialStore:
    """Store PostgreSQL passwords in the OS credential vault, never in the profile file."""

    service_name = "AIOpenStudio PostgreSQL"

    @staticmethod
    def credential_key(profile: PostgresConnectionProfile) -> str:
        return f"{profile.username}@{profile.host}:{profile.port}/{profile.database}"

    def load(self, profile: PostgresConnectionProfile) -> str | None:
        try:
            import keyring
        except ImportError:
            return None
        password = keyring.get_password(self.service_name, self.credential_key(profile))
        return password if isinstance(password, str) else None

    def save(self, profile: PostgresConnectionProfile, password: str) -> None:
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError(
                "El almacén seguro no está instalado; instala el extra opcional 'postgres'."
            ) from error
        keyring.set_password(self.service_name, self.credential_key(profile), password)

    def delete(self, profile: PostgresConnectionProfile) -> None:
        try:
            import keyring
            from keyring.errors import PasswordDeleteError
        except ImportError:
            return
        try:
            keyring.delete_password(self.service_name, self.credential_key(profile))
        except PasswordDeleteError:
            return
