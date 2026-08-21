# Validación de persistencia PostgreSQL opcional

> Estado: almacenamiento local, outbox, configuración, UI, contratos, repositorio SQLAlchemy y
> migración Alembic implementados el 21 de agosto de 2026. La batería segura está aprobada. La
> el extra `postgres` fue instalado y sus imports aprobados el 21 de agosto de 2026. La conexión
> real permanece pendiente de la configuración manual de credenciales; ningún comando ejecutado
> durante la implementación se conectó al servidor local.

## Pruebas seguras

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy src
```

Estas pruebas no importan el driver PostgreSQL, no ejecutan Alembic y no abren conexiones de red.
Cubren persistencia SQLite, deduplicación del outbox, perfiles sin secretos y regresiones de las
suites.

## Instalación opcional aprobada

El extra ya fue instalado con autorización explícita mediante:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
```

Quedaron verificados Alembic 1.19.1, psycopg 3.3.4, SQLAlchemy 2.0.52 y el backend seguro
`WinVaultKeyring`. No se instaló otro servidor, ni se descargaron modelos o repositorios.

## Configuración manual

1. Crear o elegir una base vacía. AIOpenStudio crea tablas, no la base ni el servidor.
2. Revisar `listen_addresses`, `pg_hba.conf`, firewall y TLS antes de introducir credenciales.
3. Abrir `Configuración → Conexión PostgreSQL…`.
4. Completar host, puerto, base, usuario, contraseña, SSL y timeout.
5. Mantener activa `Autocrear o actualizar tablas mediante Alembic` para una base vacía.
6. Usar `Probar conexión`; sólo `Conectar y guardar` habilita la réplica persistente.

El perfil sin contraseña vive bajo `data/runtime/database/`, ignorado por Git. Para administrar el
secreto manualmente se puede definir `AIOPENSTUDIO_DATABASE_PASSWORD` en `.env`; si se marca la
opción de recordar, keyring usa el almacén seguro del sistema operativo.

## Integración optativa

La prueba exige activación y una base dedicada:

```powershell
$env:AIOPENSTUDIO_RUN_POSTGRES_TESTS = "1"
$env:AIOPENSTUDIO_POSTGRES_HOST = "127.0.0.1"
$env:AIOPENSTUDIO_POSTGRES_PORT = "5432"
$env:AIOPENSTUDIO_POSTGRES_DATABASE = "aiopenstudio_validation"
$env:AIOPENSTUDIO_POSTGRES_USERNAME = "usuario_dedicado"
$env:AIOPENSTUDIO_DATABASE_PASSWORD = "<definir-manualmente>"
$env:AIOPENSTUDIO_POSTGRES_SSL_MODE = "prefer"
& .\.venv\Scripts\python.exe -m pytest -q -m postgres_integration
```

Debe comprobar migración hasta `20260821_secondary_persistence`, escritura/lectura de configuración
y ejecución, ausencia de secretos en salida y cierre limpio. La prueba no elimina tablas ni la base.

## Pasada manual

1. Arrancar sin perfil y confirmar que no aparece advertencia y SQLite funciona.
2. Seleccionar `Solo SQLite` directamente bajo `Configuración` y confirmar que el siguiente
   arranque no intenta conectar.
3. Seleccionar `SQLite + réplica PostgreSQL`, conectar y comprobar que la ejecución existe tanto
   en SQLite como en PostgreSQL después de vaciar el outbox.
4. Seleccionar `PostgreSQL principal`, conectar y comprobar que una ejecución nueva aparece en
   PostgreSQL pero no se duplica en SQLite.
5. Con PostgreSQL principal seleccionado, deshabilitar la conexión y comprobar la advertencia. Una
   ejecución posterior debe guardarse en SQLite y quedar pendiente en el outbox; el perfil debe
   conservar `postgres_primary` hasta que el usuario cambie el modo manualmente.
6. Probar credenciales inválidas y confirmar error sanitizado sin bloquear la UI.
7. Conectar una base vacía con autocreación y reiniciar la aplicación.
8. Confirmar reconexión automática y réplica de una operación LLM, Whisper y Fooocus.
9. Detener PostgreSQL, ejecutar otra operación y comprobar que queda en el outbox local.
10. Reiniciar PostgreSQL, usar `Aplicar / conectar` y confirmar vaciado del pendiente.
11. Deshabilitar PostgreSQL, reiniciar y confirmar que no se intenta conectar en modo replicado.
12. Olvidar la credencial y confirmar que el perfil JSON nunca contiene la contraseña.
