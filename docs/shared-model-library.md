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
python scripts/model_library.py download llm.gemma4-e4b-it-qat
```

Descargar varios:

```powershell
python scripts/model_library.py download `
  llm.qwen3-8b-q4 `
  llm.qwen2.5-coder-7b-q4
```

Aportar los cuatro activos auxiliares requeridos por Fooocus v2.5.5:

```powershell
python scripts/model_library.py download `
  image.fooocus-xl-vae-approx `
  image.fooocus-sd15-vae-approx `
  image.fooocus-xl-to-v1-interposer `
  image.fooocus-prompt-expansion
```

Este lote no descarga checkpoints. Conserva los nombres locales exactos que espera Fooocus y
registra el SHA-256 de cada archivo terminado.

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

## Flujo operativo recomendado con Ollama

Este es el procedimiento de referencia para descargar un modelo con trazabilidad y comprobarlo
después en ambos catálogos. Debe ejecutarse desde una terminal nueva para que `PATH` y las
variables de usuario vigentes estén disponibles.

### 1. Comprobar el entorno

```powershell
ollama --version
[Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User")
```

La segunda instrucción debe devolver la raíz compartida de Ollama:

```text
C:\Users\fario\Documents\AIModels\ollama
```

Antes de iniciar una descarga con el script, cerrar completamente la aplicación de Ollama desde
la bandeja de Windows. El script actual levanta su propio servidor temporal en
`127.0.0.1:11435`; evitar dos procesos activos sobre el mismo almacén reduce el riesgo de
competencia durante la escritura de blobs y manifiestos.

### 2. Consultar el catálogo de candidatos

```powershell
.\.venv\Scripts\python.exe scripts\model_library.py list --kind llm
```

El identificador de AIOpenStudio no siempre coincide con el nombre que usa el runtime. Por
ejemplo, `llm.phi4-mini-3.8b-q4` se descarga en Ollama como
`phi4-mini:3.8b-q4_K_M`. Ambos valores se conservan en el manifiesto y SQLite.

Para Gemma 4, `llm.gemma4-e4b-it-qat` se resuelve como `gemma4:e4b-it-qat`. Es el artefacto QAT
oficial de Ollama de aproximadamente 6,1 GB, reportado como Q6_K; no debe describirse como Q4 o Q5.
E4B significa unos 4,5B parámetros efectivos y aproximadamente 8B totales con embeddings. En la
GPU local de 8 GB se debe comenzar con contexto de 2K/4K, sin otra carga residente y observando el
monitor. Incorporarlo al manifiesto no descarga el modelo ni implica que ya esté validado.

### 3. Descargar con trazabilidad

```powershell
.\.venv\Scripts\python.exe scripts\model_library.py download llm.phi4-mini-3.8b-q4
```

Revisar el tamaño y la licencia mostrados y escribir `DESCARGAR` para confirmar. Una instalación
exitosa produce cuatro resultados coordinados:

1. Ollama escribe sus blobs y manifiestos en la carpeta compartida.
2. La tabla `artifacts` registra el modelo, su referencia de runtime, tamaño y digest.
3. `download_events` registra el resultado del intento.
4. El checklist temporal y el bloque administrado del `.env` se actualizan.

No cerrar la terminal mientras la descarga está activa. Si falla, el artefacto no se incorpora a
`artifacts`; el error sí queda registrado como evento para diagnóstico.

### 4. Verificar el catálogo de AIOpenStudio

```powershell
.\.venv\Scripts\python.exe scripts\model_library.py status
```

La salida debe incluir el artefacto instalado. La misma información se refleja en
`C:\Users\fario\Documents\AIModels\download-checklist.md`; SQLite sigue siendo la fuente de
verdad.

### 5. Verificar el catálogo de Ollama

Abrir nuevamente Ollama y ejecutar desde una terminal nueva:

```powershell
ollama list
(Invoke-RestMethod http://127.0.0.1:11434/api/tags).models |
    Select-Object name, size, digest
```

El modelo debería aparecer con su referencia de runtime. Si la aplicación gráfica estaba abierta
antes de la descarga, cerrar y abrir el selector de modelos o reiniciar Ollama para refrescar la
vista.

### 6. Ejecutar y liberar el modelo

```powershell
ollama run phi4-mini:3.8b-q4_K_M "Responde solamente: modelo operativo"
ollama ps
ollama stop phi4-mini:3.8b-q4_K_M
```

`ollama run` valida la generación; `ollama ps` permite observar si el modelo permanece cargado y
`ollama stop` lo libera de RAM/VRAM sin eliminar sus archivos.

### Direcciones de sincronización

- **Script → Ollama:** sí. El script usa `ollama pull`, por lo que Ollama reconoce los modelos
  descargados en el almacén compartido.
- **Script → SQLite:** sí. Solo una descarga verificada genera o actualiza la fila de `artifacts`.
- **Interfaz de Ollama → SQLite:** todavía no. Una descarga iniciada desde la aplicación gráfica o
  con un `ollama pull` manual queda disponible para Ollama, pero no se incorpora automáticamente
  al catálogo de AIOpenStudio.

Hasta implementar una operación de reconciliación, usar el script para toda descarga que deba
quedar inventariada por AIOpenStudio. No mover, renombrar ni deduplicar manualmente archivos dentro
de `AIModels\ollama`; esa estructura pertenece a Ollama.

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
