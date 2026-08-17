# Biblioteca compartida de modelos

AIOpenStudio usa una biblioteca de pesos externa a los repositorios para evitar copias redundantes
entre proyectos. En el equipo actual su raíz es:

```text
C:\Users\fario\Documents\AIModels
```

La biblioteca no reserva espacio en disco ni descarga por sí sola. El script versionado prepara
la estructura y descarga únicamente los artefactos seleccionados por el usuario.

## Estructura

```text
AIModels/
├── catalog/
│   └── model-library.sqlite3
├── ollama/                  # Estructura interna administrada por Ollama
├── huggingface/             # Caché compartida del Hub
├── whisper/                 # Snapshots utilizables por faster-whisper
├── fooocus/
│   ├── checkpoints/
│   └── loras/
├── embeddings/              # Snapshots de modelos de embeddings
├── manifests/               # Copia del manifiesto usado por el script
├── cache/                   # Caché general reservada para adaptadores
├── temp/                    # Parciales y logs temporales
└── download-checklist.md    # Vista temporal y regenerable del estado
```

Las rutas almacenadas en SQLite son relativas a `AIModels/`. La única ruta dependiente del equipo
es `AIOPENSTUDIO_MODEL_LIBRARY_ROOT` en `.env`.

## Fuentes de configuración

| Fuente | Responsabilidad |
|---|---|
| `.env` | Raíz local, subrutas relativas y variables directas de Ollama/Hugging Face |
| `models/download-catalog.json` | Candidatos, proveedor, fuente, variante, licencia y destino esperado |
| `schemas/model-library.sql` | Contrato versionable de la base compartida |
| `model-library.sqlite3` | Artefactos instalados y eventos de descarga |
| `core/model_library.py` | Modelos Pydantic portables para configuración, manifiesto y filas instaladas |
| `download-checklist.md` | Presentación temporal; nunca es la fuente de verdad |

El catálogo de modelos compartidos es independiente de `data/runtime/memory.sqlite3`, que guarda
conversaciones, resúmenes y memoria propia de AIOpenStudio.

## Inicialización

Desde el repositorio y con el entorno Python activo:

```powershell
python scripts/model_library.py init
```

Este comando no usa la red. Crea las carpetas, inicializa SQLite, copia el manifiesto vigente,
actualiza el bloque administrado del `.env` y genera el checklist.

Para usar otra ubicación:

```powershell
python scripts/model_library.py --root D:\AIModels init
```

## Consulta y descarga

Listar todo o una familia:

```powershell
python scripts/model_library.py list
python scripts/model_library.py list --kind llm
python scripts/model_library.py list --kind speech
```

Descargar un artefacto explícito:

```powershell
python scripts/model_library.py download llm.phi4-mini-3.8b-q4
```

Descargar varios:

```powershell
python scripts/model_library.py download `
  llm.qwen3-8b-q4 `
  llm.qwen2.5-coder-7b-q4
```

Descargar una categoría o todo el manifiesto:

```powershell
python scripts/model_library.py download --kind embedding
python scripts/model_library.py download --all
```

Antes de comenzar, el script muestra cantidad, tamaño conocido y licencias. Se debe escribir
`DESCARGAR`. `--yes` omite esa confirmación y, por tanto, solo debe usarse después de revisar el
manifiesto y los términos de todos los elementos seleccionados.

El script continúa con el siguiente elemento si uno falla. `--fail-fast` detiene el lote en el
primer error y `--force` vuelve a validar o descargar elementos ya registrados.

## Comportamiento por proveedor

### Ollama

El script no instala Ollama. Si se selecciona un artefacto Ollama, busca el ejecutable en `PATH`,
levanta temporalmente un servidor propio en `127.0.0.1:11435`, configura `OLLAMA_MODELS` con la
carpeta central y lo detiene al finalizar. Rechaza el proceso si ese puerto ya está ocupado porque
no podría garantizar qué directorio usa el servidor ajeno.

La descarga se realiza con `ollama pull`. Después consulta `/api/tags` y guarda el nombre efectivo,
tamaño y digest informados por Ollama. Los blobs siguen siendo administrados por Ollama; otras
aplicaciones deben referirse al `runtime_reference`, no a archivos internos.

### Hugging Face

Requiere `huggingface-hub`, ya incluido en las dependencias de modelos del proyecto. El script
resuelve `main` a un commit concreto antes de descargar y registra ese commit en SQLite. Los
snapshots utilizables se escriben en `whisper/` o `embeddings/`, y la caché compartida vive bajo
`huggingface/`.

Si un repositorio exige autenticación, el token se recibe exclusivamente desde `HF_TOKEN` y no se
escribe en `.env`, SQLite, checklist ni logs.

### Archivos HTTP de Fooocus

Los enlaces provienen de los presets oficiales de Fooocus. Se descargan primero como
`<nombre>.partial`, admiten reanudación cuando el servidor acepta rangos y se renombran solo al
completar. El script calcula SHA-256 antes de registrar el archivo.

Algunas licencias de checkpoints son específicas del modelo. Es obligatorio revisar el enlace de
licencia mostrado en el manifiesto antes de confirmar. La presencia en un preset oficial no
reemplaza esa revisión.

## SQLite y portabilidad

La tabla `artifacts` contiene únicamente instalaciones exitosas. Entre otros campos registra:

- identificador estable, familia, variante y cuantización;
- proveedor, fuente y referencia del runtime;
- ruta relativa a la raíz;
- licencia y fuente oficial;
- tamaño, SHA-256 o digest cuando corresponde;
- revisión resuelta, capacidades y fechas de instalación/verificación.

`download_events` conserva el resultado de cada intento. El checklist se puede borrar y regenerar
con:

```powershell
python scripts/model_library.py status
```

Para que otra aplicación lea la biblioteca hacen falta:

1. Su propio `.env` con la raíz correcta.
2. Una copia compatible de `schemas/model-library.sql` para inicialización/migración.
3. Los modelos de `src/aiopenstudio/core/model_library.py` o un contrato equivalente.
4. Acceso de lectura a `model-library.sqlite3` y a la raíz de archivos.

Copiar solo SQLite no copia los pesos. Mover la biblioteca completa requiere cambiar únicamente
`AIOPENSTUDIO_MODEL_LIBRARY_ROOT`, `OLLAMA_MODELS` y `HF_HOME`; las filas relativas siguen siendo
válidas.

No deben ejecutarse dos procesos de descarga sobre la misma biblioteca al mismo tiempo. Los
consumidores pueden abrir SQLite en lectura mientras no dependan de una fila que todavía se está
instalando.

## Incorporar un candidato nuevo

1. Añadir una entrada a `models/download-catalog.json` con un `artifact_id` único.
2. Usar una ruta local relativa sin `..`.
3. Registrar fuente oficial, licencia, variante y tamaño esperado cuando se conozca.
4. Ejecutar `list` para validar el manifiesto sin usar red.
5. Descargar el ID concreto y revisar SQLite/checklist.

El import de los contratos y la carga del manifiesto no crean directorios ni descargan modelos.
