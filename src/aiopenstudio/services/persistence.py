"""Local-first persistence with optional PostgreSQL outbox replication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import SecretStr

from aiopenstudio.core.contracts import (
    ArtifactRecord,
    ConnectionProfileStore,
    CredentialStore,
    ExecutionRecord,
    ExecutionStatus,
    LocalPersistenceStore,
    PersistenceConnectionStatus,
    PersistenceMode,
    PersistenceState,
    PostgresConnectionProfile,
    PostgresConnectionResult,
    SecondaryPersistenceRepository,
    StoredConfiguration,
)


class PersistenceService:
    """Coordinate local, replicated and PostgreSQL-primary persistence modes."""

    def __init__(
        self,
        local: LocalPersistenceStore,
        profile_store: ConnectionProfileStore,
        credential_store: CredentialStore,
        secondary_factory: Callable[
            [PostgresConnectionProfile, str], SecondaryPersistenceRepository
        ],
        *,
        environment_password: SecretStr | None = None,
    ) -> None:
        self._local = local
        self._profiles = profile_store
        self._credentials = credential_store
        self._secondary_factory = secondary_factory
        self._environment_password = environment_password
        self._repository: SecondaryPersistenceRepository | None = None
        self._status = PersistenceConnectionStatus.DISCONNECTED
        self._message = "PostgreSQL no está conectado."
        self._fallback_active = False
        self._logger = logging.getLogger("aiopenstudio.persistence")
        self._lock = asyncio.Lock()

    async def state(self) -> PersistenceState:
        profile = await asyncio.to_thread(self._profiles.load)
        pending = await asyncio.to_thread(self._local.pending_outbox_count)
        status = self._status
        message = self._message
        if not profile.enabled:
            status = PersistenceConnectionStatus.DISABLED
            if profile.mode is PersistenceMode.SQLITE_ONLY:
                message = "Modo Solo SQLite activo."
        return PersistenceState(
            profile=profile,
            status=status,
            message=message,
            pending_operations=pending,
            fallback_active=(
                profile.mode is PersistenceMode.POSTGRES_PRIMARY
                and (self._fallback_active or status is not PersistenceConnectionStatus.CONNECTED)
            ),
        )

    async def test_connection(
        self,
        profile: PostgresConnectionProfile,
        password: str,
    ) -> PostgresConnectionResult:
        stored_password = await asyncio.to_thread(self._credentials.load, profile)
        resolved_password = password or stored_password or ""
        if not resolved_password and self._environment_password is not None:
            resolved_password = self._environment_password.get_secret_value()
        if not resolved_password:
            return PostgresConnectionResult(
                success=False,
                status=PersistenceConnectionStatus.ERROR,
                message="Falta la contraseña PostgreSQL para probar la conexión.",
            )
        repository = self._secondary_factory(profile, resolved_password)
        try:
            return await asyncio.to_thread(repository.connect)
        finally:
            await asyncio.to_thread(repository.dispose)

    async def connect(
        self,
        profile: PostgresConnectionProfile,
        password: str = "",
    ) -> PostgresConnectionResult:
        async with self._lock:
            enabled_profile = profile.model_copy(update={"enabled": True})
            self._fallback_active = False
            self._status = PersistenceConnectionStatus.CHECKING
            self._message = "Verificando PostgreSQL…"
            stored_password = await asyncio.to_thread(
                self._credentials.load, enabled_profile
            )
            resolved_password = password or stored_password or ""
            if not resolved_password and self._environment_password is not None:
                resolved_password = self._environment_password.get_secret_value()
            if not resolved_password:
                result = PostgresConnectionResult(
                    success=False,
                    status=PersistenceConnectionStatus.ERROR,
                    message=(
                        "Falta la contraseña PostgreSQL. Introdúcela o configura "
                        "AIOPENSTUDIO_DATABASE_PASSWORD."
                    ),
                )
                await asyncio.to_thread(self._profiles.save, enabled_profile)
                self._status = PersistenceConnectionStatus.DISCONNECTED
                self._message = self._connection_failure_message(enabled_profile, result.message)
                self._fallback_active = (
                    enabled_profile.mode is PersistenceMode.POSTGRES_PRIMARY
                )
                return result.model_copy(update={"message": self._message})

            repository = self._secondary_factory(enabled_profile, resolved_password)
            result = await asyncio.to_thread(repository.connect)
            await asyncio.to_thread(self._profiles.save, enabled_profile)
            if not result.success:
                await asyncio.to_thread(repository.dispose)
                self._replace_repository(None)
                self._status = PersistenceConnectionStatus.DISCONNECTED
                self._message = self._connection_failure_message(enabled_profile, result.message)
                self._fallback_active = (
                    enabled_profile.mode is PersistenceMode.POSTGRES_PRIMARY
                )
                return result.model_copy(update={"message": self._message})
            if enabled_profile.remember_password and password:
                try:
                    await asyncio.to_thread(
                        self._credentials.save, enabled_profile, resolved_password
                    )
                except Exception as error:
                    await asyncio.to_thread(repository.dispose)
                    message = " ".join(str(error).split())[:600]
                    self._status = PersistenceConnectionStatus.DISCONNECTED
                    self._message = message
                    return PostgresConnectionResult(
                        success=False,
                        status=PersistenceConnectionStatus.ERROR,
                        message=message,
                    )
            elif not enabled_profile.remember_password:
                await asyncio.to_thread(self._credentials.delete, enabled_profile)
            self._replace_repository(repository)
            self._status = PersistenceConnectionStatus.CONNECTED
            self._message = result.message
            self._fallback_active = False
            if enabled_profile.synchronize_existing:
                await asyncio.to_thread(self._local.queue_existing_for_secondary)
                enabled_profile = enabled_profile.model_copy(
                    update={"synchronize_existing": False}
                )
                await asyncio.to_thread(self._profiles.save, enabled_profile)
            await self._synchronize_locked()
            if self._status is not PersistenceConnectionStatus.CONNECTED:
                return PostgresConnectionResult(
                    success=False,
                    status=PersistenceConnectionStatus.ERROR,
                    message=self._message,
                )
            return result

    async def reconnect(self) -> PostgresConnectionResult | None:
        profile = await asyncio.to_thread(self._profiles.load)
        if not profile.enabled:
            self._status = PersistenceConnectionStatus.DISABLED
            if profile.mode is PersistenceMode.SQLITE_ONLY:
                self._fallback_active = False
                self._message = "Modo Solo SQLite activo."
                return None
            if profile.mode is PersistenceMode.POSTGRES_PRIMARY:
                self._fallback_active = True
                self._message = self._primary_fallback_message(
                    "PostgreSQL principal está deshabilitado."
                )
                return PostgresConnectionResult(
                    success=False,
                    status=PersistenceConnectionStatus.DISABLED,
                    message=self._message,
                )
            self._fallback_active = False
            self._message = "PostgreSQL está deshabilitado."
            return None
        return await self.connect(profile)

    async def configure_sqlite_only(self, profile: PostgresConnectionProfile) -> None:
        async with self._lock:
            local_profile = profile.model_copy(
                update={"enabled": False, "mode": PersistenceMode.SQLITE_ONLY}
            )
            await asyncio.to_thread(self._profiles.save, local_profile)
            self._status = PersistenceConnectionStatus.DISABLED
            self._fallback_active = False
            self._message = "Modo Solo SQLite activo."
            self._replace_repository(None)

    async def set_mode(self, mode: PersistenceMode) -> PersistenceState:
        async with self._lock:
            profile = await asyncio.to_thread(self._profiles.load)
            profile = profile.model_copy(
                update={
                    "mode": mode,
                    "enabled": False if mode is PersistenceMode.SQLITE_ONLY else profile.enabled,
                }
            )
            await asyncio.to_thread(self._profiles.save, profile)
            if mode is PersistenceMode.SQLITE_ONLY:
                self._status = PersistenceConnectionStatus.DISABLED
                self._fallback_active = False
                self._message = "Modo Solo SQLite activo."
                self._replace_repository(None)
            elif self._repository is not None:
                self._status = PersistenceConnectionStatus.CONNECTED
                self._fallback_active = False
                self._message = (
                    "PostgreSQL conectado como almacenamiento principal."
                    if mode is PersistenceMode.POSTGRES_PRIMARY
                    else "PostgreSQL conectado como réplica secundaria."
                )
            elif mode is PersistenceMode.POSTGRES_PRIMARY:
                self._status = (
                    PersistenceConnectionStatus.DISCONNECTED
                    if profile.enabled
                    else PersistenceConnectionStatus.DISABLED
                )
                self._fallback_active = True
                self._message = self._primary_fallback_message(
                    "PostgreSQL principal no tiene una conexión activa."
                )
            else:
                self._status = (
                    PersistenceConnectionStatus.DISCONNECTED
                    if profile.enabled
                    else PersistenceConnectionStatus.DISABLED
                )
                self._fallback_active = False
                self._message = (
                    "PostgreSQL está desconectado."
                    if profile.enabled
                    else "PostgreSQL está deshabilitado."
                )
        return await self.state()

    async def disconnect(self, *, disable: bool = False) -> None:
        async with self._lock:
            profile = await asyncio.to_thread(self._profiles.load)
            if disable:
                profile = profile.model_copy(update={"enabled": False})
                await asyncio.to_thread(self._profiles.save, profile)
                self._status = PersistenceConnectionStatus.DISABLED
                if profile.mode is PersistenceMode.POSTGRES_PRIMARY:
                    self._fallback_active = True
                    self._message = self._primary_fallback_message(
                        "PostgreSQL principal fue deshabilitado."
                    )
                else:
                    self._fallback_active = False
                    self._message = "PostgreSQL está deshabilitado."
            else:
                self._status = PersistenceConnectionStatus.DISCONNECTED
                self._fallback_active = profile.mode is PersistenceMode.POSTGRES_PRIMARY
                self._message = (
                    self._primary_fallback_message("PostgreSQL principal fue desconectado.")
                    if self._fallback_active
                    else "PostgreSQL está desconectado."
                )
            self._replace_repository(None)

    async def forget_credentials(self) -> None:
        profile = await asyncio.to_thread(self._profiles.load)
        await asyncio.to_thread(self._credentials.delete, profile)
        await asyncio.to_thread(
            self._profiles.save,
            profile.model_copy(update={"remember_password": False}),
        )

    async def save_configuration(self, configuration: StoredConfiguration) -> None:
        profile = await asyncio.to_thread(self._profiles.load)
        if profile.mode is PersistenceMode.POSTGRES_PRIMARY:
            async with self._lock:
                repository = self._repository
                if self._status is PersistenceConnectionStatus.CONNECTED and repository:
                    try:
                        await asyncio.to_thread(repository.save_configuration, configuration)
                        return
                    except Exception as error:
                        self._activate_primary_fallback(error)
                await asyncio.to_thread(
                    self._local.save_configuration,
                    configuration,
                    enqueue_secondary=True,
                )
            return
        await asyncio.to_thread(
            self._local.save_configuration,
            configuration,
            enqueue_secondary=(
                profile.enabled and profile.mode is PersistenceMode.SQLITE_REPLICATED
            ),
        )
        if self._status is PersistenceConnectionStatus.CONNECTED:
            async with self._lock:
                await self._synchronize_locked()

    async def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
    ) -> None:
        self._logger.info(
            "execution.status_changed",
            extra={
                "component": "persistence",
                "operation_id": execution.operation_id,
                "suite": execution.suite,
                "runtime": execution.runtime,
                "model": execution.model_key,
                "status": execution.status.value,
            },
        )
        profile = await asyncio.to_thread(self._profiles.load)
        if profile.mode is PersistenceMode.POSTGRES_PRIMARY:
            async with self._lock:
                repository = self._repository
                if self._status is PersistenceConnectionStatus.CONNECTED and repository:
                    try:
                        await asyncio.to_thread(
                            repository.save_execution, execution, artifacts
                        )
                        return
                    except Exception as error:
                        self._activate_primary_fallback(error)
                await asyncio.to_thread(
                    self._local.save_execution,
                    execution,
                    artifacts,
                    enqueue_secondary=True,
                )
            return
        await asyncio.to_thread(
            self._local.save_execution,
            execution,
            artifacts,
            enqueue_secondary=(
                profile.enabled and profile.mode is PersistenceMode.SQLITE_REPLICATED
            ),
        )
        if self._status is PersistenceConnectionStatus.CONNECTED:
            async with self._lock:
                await self._synchronize_locked()

    async def synchronize(self) -> int:
        async with self._lock:
            return await self._synchronize_locked()

    async def list_executions(self, limit: int = 100) -> Sequence[ExecutionRecord]:
        profile = await asyncio.to_thread(self._profiles.load)
        if (
            profile.mode is PersistenceMode.POSTGRES_PRIMARY
            and self._status is PersistenceConnectionStatus.CONNECTED
            and self._repository is not None
        ):
            return await asyncio.to_thread(self._repository.list_executions, limit)
        return await asyncio.to_thread(self._local.list_executions, limit)

    async def reconcile_interrupted(self, started_before: datetime) -> int:
        reconciled = 0
        for execution in await self.list_executions(limit=500):
            if execution.status not in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
                continue
            if execution.started_at >= started_before:
                continue
            await self.save_execution(
                execution.model_copy(
                    update={
                        "status": ExecutionStatus.INTERRUPTED,
                        "finished_at": datetime.now(UTC),
                        "error_message": (
                            "La ejecución pertenecía a una sesión anterior y no registró "
                            "un cierre terminal."
                        ),
                    }
                )
            )
            reconciled += 1
        return reconciled

    async def close(self) -> None:
        async with self._lock:
            self._replace_repository(None)

    async def _synchronize_locked(self) -> int:
        repository = self._repository
        if repository is None:
            return 0
        synchronized = 0
        while True:
            entries = await asyncio.to_thread(self._local.pending_outbox, 100)
            if not entries:
                return synchronized
            for entry in entries:
                try:
                    if entry.entity_kind == "configuration":
                        configuration = StoredConfiguration.model_validate(
                            entry.payload["configuration"]
                        )
                        await asyncio.to_thread(repository.save_configuration, configuration)
                    elif entry.entity_kind == "execution":
                        execution = ExecutionRecord.model_validate(entry.payload["execution"])
                        raw_artifacts = entry.payload.get("artifacts", [])
                        if not isinstance(raw_artifacts, list):
                            raise ValueError("La lista de artefactos del outbox no es válida.")
                        artifacts = tuple(
                            ArtifactRecord.model_validate(item) for item in raw_artifacts
                        )
                        await asyncio.to_thread(repository.save_execution, execution, artifacts)
                    await asyncio.to_thread(self._local.mark_outbox_synced, entry.event_id)
                    synchronized += 1
                except Exception as error:
                    message = " ".join(str(error).split())[:1_000]
                    await asyncio.to_thread(
                        self._local.mark_outbox_failed, entry.event_id, message
                    )
                    self._status = PersistenceConnectionStatus.DISCONNECTED
                    profile = await asyncio.to_thread(self._profiles.load)
                    base_message = (
                        f"PostgreSQL se desconectó durante la sincronización: {message}"
                    )
                    self._fallback_active = (
                        profile.mode is PersistenceMode.POSTGRES_PRIMARY
                    )
                    self._message = (
                        self._primary_fallback_message(base_message)
                        if self._fallback_active
                        else base_message
                    )
                    self._replace_repository(None)
                    return synchronized

    def _replace_repository(
        self, repository: SecondaryPersistenceRepository | None
    ) -> None:
        previous = self._repository
        self._repository = repository
        if previous is not None and previous is not repository:
            previous.dispose()

    def _activate_primary_fallback(self, error: Exception) -> None:
        message = " ".join(str(error).split())[:1_000]
        self._status = PersistenceConnectionStatus.DISCONNECTED
        self._fallback_active = True
        self._message = self._primary_fallback_message(
            f"Falló una escritura en PostgreSQL principal: {message}"
        )
        self._replace_repository(None)

    def _connection_failure_message(
        self, profile: PostgresConnectionProfile, message: str
    ) -> str:
        if profile.mode is PersistenceMode.POSTGRES_PRIMARY:
            return self._primary_fallback_message(message)
        return message

    @staticmethod
    def _primary_fallback_message(reason: str) -> str:
        return (
            f"{reason} La sesión usará SQLite como fallback. "
            "La preferencia PostgreSQL principal se conservará; abre Configuración → "
            "Persistencia para reconectar o volver manualmente a un modo SQLite."
        )
