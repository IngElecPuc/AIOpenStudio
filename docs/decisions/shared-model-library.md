# Biblioteca externa y compartida de modelos

## Contexto

Los mismos LLM, modelos de voz, embeddings y checkpoints de imagen serán utilizados por varios
proyectos locales. Guardarlos dentro de cada repositorio produciría copias redundantes, rutas
difíciles de migrar y riesgo de versionar pesos accidentalmente.

## Decisión

- Mantener una biblioteca externa común cuya raíz se configura mediante `.env`.
- Usar una raíz configurable; `data/models` es el valor portable predeterminado y una biblioteca
  externa puede indicarse mediante `--root` o configuración local ignorada.
- Guardar en SQLite únicamente metadatos y rutas relativas; los pesos permanecen como archivos.
- Mantener separado el catálogo compartido de la memoria conversacional propia de cada aplicación.
- Respetar el almacenamiento interno de Ollama y registrar sus modelos mediante referencias y
  digests, no mediante rutas a blobs.
- Resolver revisiones de Hugging Face antes de registrarlas.
- Usar un manifiesto versionado como lista de candidatos, sin convertirlo en autorización de
  descarga.
- Actualizar `.env`, SQLite y un checklist regenerable después de cada instalación exitosa.

## Alternativas descartadas

- Un directorio de pesos por repositorio: duplica espacio y dificulta actualizaciones.
- Rutas absolutas por modelo en SQLite: impiden mover la biblioteca como unidad.
- Una sola SQLite para catálogo compartido y conversaciones: mezcla ciclos de vida y privacidad
  diferentes.
- Enlaces simbólicos como contrato principal: tienen fricción de permisos en Windows y no expresan
  proveedor, licencia, versión o integridad.

## Consecuencias

- Los proyectos comparten pesos sin copiar archivos.
- Mover la biblioteca exige cambiar la raíz, no reescribir sus filas.
- La base y los modelos Pydantic forman un contrato que otras aplicaciones pueden reutilizar.
- La biblioteca requiere copias de seguridad separadas para metadatos y pesos.
- Ollama debe arrancar con `OLLAMA_MODELS` coherente con la raíz compartida.
- Los consumidores deben tolerar artefactos ausentes y no inferir instalación desde el manifiesto.
- La evolución del esquema necesitará migraciones explícitas a partir de `user_version = 1`.

## Implementación relacionada

- `docs/shared-model-library.md`
- `models/download-catalog.json`
- `schemas/model-library.sql`
- `scripts/model_library.py`
- `src/aiopenstudio/core/model_library.py`
