# Modos de persistencia SQLite y PostgreSQL

- Estado: implementada; validación contra servidor real pendiente.
- Fecha: 2026-08-21.

## Contexto

AIOpenStudio debe funcionar en equipos sin PostgreSQL. SQLite ya conserva el catálogo y la memoria
local, mientras que un servidor PostgreSQL puede aportar un historial centralizado y más durable.
Una pérdida de red, credenciales inválidas o un servidor ausente no puede bloquear las suites ni
perder ejecuciones terminadas.

## Decisión

- Ofrecer tres modos explícitos: solo SQLite, SQLite autoritativo con réplica y PostgreSQL
  autoritativo con fallback SQLite.
- En PostgreSQL principal, escribir configuraciones, ejecuciones y metadatos directamente en el
  repositorio remoto mientras esté conectado; no duplicar esas escrituras en SQLite.
- Si PostgreSQL principal está deshabilitado, no reconecta o falla al escribir, activar SQLite como
  fallback durable y encolar esos cambios para sincronización posterior.
- Registrar cada cambio replicable en un outbox SQLite dentro de la misma transacción que el dato
  local. Los upserts PostgreSQL son idempotentes y el outbox se conserva durante desconexiones.
- Mantener separados el estado del perfil (`habilitado/deshabilitado`) y el de la conexión. Un fallo
  al iniciar deja la preferencia intacta y la conexión desconectada; la aplicación continúa local.
  Nunca degradar silenciosa y permanentemente el modo guardado: la interfaz advierte el fallback y
  exige que el usuario reconecte o seleccione manualmente un modo SQLite.
- Administrar el esquema PostgreSQL con Alembic. La autocreación/actualización de tablas es una
  opción explícita del perfil y actúa dentro de una base ya creada, nunca crea el servidor ni la
  base de datos.
- Persistir host, puerto, base, usuario, SSL y políticas en
  `data/runtime/database/postgres-profile.json`. La contraseña se obtiene del almacén seguro del
  sistema o de `AIOPENSTUDIO_DATABASE_PASSWORD`; nunca se escribe en ese archivo.
- Guardar archivos grandes fuera de ambas bases. PostgreSQL conserva rutas, hashes y metadatos, no
  imágenes, audios, pesos ni otros binarios.
- No replicar prompts, respuestas o transcripciones por defecto. El historial técnico utiliza
  hashes, tiempos, estados, métricas y conteos.

## Consecuencias

- PostgreSQL ausente o caído no impide arrancar, generar, transcribir o conversar; en modo principal
  la continuidad queda identificada explícitamente como fallback.
- La sincronización inicial de datos existentes es explícita y puede ser costosa.
- La sincronización de fallback y réplica es unidireccional, SQLite → PostgreSQL; no existe
  resolución de ediciones remotas ni descarga automática de configuración.
- El extra `postgres` instala Alembic, psycopg y keyring. La aplicación base no los importa ni los
  necesita mientras PostgreSQL esté deshabilitado.
- Antes de usar un servidor se deben revisar exposición, TLS, firewall, `listen_addresses` y
  `pg_hba.conf`.
