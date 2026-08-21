# AIOpenStudio

AIOpenStudio será una aplicación de escritorio en Python para descubrir, ejecutar y administrar modelos de inteligencia artificial locales. La interfaz se construirá con Tkinter y tendrá suites independientes para LLM, Fooocus y Whisper. Ollama será el primer backend de la suite de LLM.

> Estado: verticales LLM y Monitor aceptadas; Whisper validada en CPU con `small`; Fooocus validado
> con generación y cancelación reales en GPU. Ninguna suite descarga pesos desde la UI.

## Objetivos

- Ejecutar varios backends locales desde una única aplicación.
- Preferir GPU cuando esté disponible y permitir mover o descargar modelos de GPU y RAM.
- Mostrar consumo y disponibilidad de GPU, VRAM, RAM y estado de los procesos.
- Mantener las suites desacopladas de los backends concretos.
- Permitir persistencia opcional en PostgreSQL mediante SQLAlchemy y validación con Pydantic.
- Crecer mediante adaptadores sin convertir la UI en el lugar donde vive la lógica de negocio.

## Suites iniciales

| Suite | Backend inicial | Propósito |
|---|---|---|
| LLM | Ollama | Chat, generación de texto y administración de modelos de lenguaje |
| Fooocus | Fooocus | Generación y gestión de imágenes |
| Whisper | faster-whisper | Transcripción, exportación y dictado para LLM |

Ollama es un backend, no una suite. Esta distinción permite incorporar otros runners de LLM en el futuro sin rediseñar la interfaz.

## Arquitectura propuesta

```text
src/aiopenstudio/
├── app.py                    # Punto de composición futuro
├── core/
│   ├── contracts/            # Interfaces y modelos compartidos
│   └── config.py             # Configuración validada
├── services/                 # Casos de uso y coordinación
│   ├── model_manager/
│   └── resource_monitor/
├── infrastructure/           # Integraciones externas
│   ├── database/
│   ├── monitoring/          # psutil, NVML, Ollama y registro en proceso
│   └── runtimes/
│       ├── ollama/
│       ├── fooocus/
│       └── whisper/
└── ui/
    ├── tabs/                 # LLM, Fooocus y Whisper
    └── widgets/
```

La dirección de dependencias será `ui -> services -> core`. La infraestructura implementará contratos de `core` y se inyectará en los servicios. El dominio no importará Tkinter, SQLAlchemy, Ollama, Fooocus ni Whisper.

## Estructura del repositorio

- `docs/`: plan, decisiones técnicas y documentación de desarrollo.
- `models/`: manifiestos y configuración local por familia; los pesos descargados se ignoran en Git.
- `schemas/`: esquemas SQLite versionables y portables.
- `scripts/`: herramientas explícitas de inicialización y descarga.
- `data/`: datos locales, cachés, entradas y salidas; no debe contener secretos.
- `src/aiopenstudio/`: código fuente de la aplicación.
- `tests/`: pruebas unitarias y de integración.

## Preparación del entorno

Requiere Python 3.12 x64 para el entorno principal. En el equipo inventariado, Python se administra desde `%LOCALAPPDATA%\Python\bin`:

```powershell
& "$env:LOCALAPPDATA\Python\bin\python3.12.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
```

Para agregar sólo la suite Whisper y la captura de micrófono a una instalación editable mínima:

```powershell
python -m pip install -e ".[whisper]"
```

Este comando no descarga pesos. La compatibilidad CUDA se valida por separado antes de usar GPU.

El transporte de la suite Fooocus puede agregarse del mismo modo:

```powershell
python -m pip install -e ".[fooocus]"
```

Este comando no instala Fooocus ni descarga checkpoints.

`requirements.txt` incluye ambos grupos para preparar de una vez el entorno principal. Los extras
son útiles para instalaciones incrementales basadas en `pip install --no-deps -e .`.

Tkinter forma parte de la instalación estándar de Python en Windows y no se instala con `pip`.

### PyTorch y GPU

El inventario confirmó una RTX 5060 Laptop, compute capability 12.0, driver 573.13 y CUDA Toolkit 12.8. `requirements.txt` selecciona la combinación oficial PyTorch 2.11.0, torchvision 0.26.0 y torchaudio 2.11.0 con wheels CUDA 12.8. Esa variante ya contiene soporte GPU; no existe otro paquete separado llamado `pytorch-gpu`.

Verificar después de instalar:

```powershell
python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'); print(torch.cuda.get_device_capability(0) if torch.cuda.is_available() else 'N/A')"
```

El monitoreo NVIDIA usará NVML cuando esté disponible. La arquitectura no asumirá NVIDIA de forma permanente: el contrato de métricas admite otros proveedores.

### Entorno aislado de Fooocus

Fooocus **no se instala en `.venv`**. La release oficial v2.5.5 fija versiones antiguas de Gradio,
Transformers y NumPy, además de una combinación PyTorch distinta de la aplicación. Para evitar que
esas restricciones rompan LLM, Whisper o el monitor, se usa este layout local ignorado por Git:

```text
data/runtime/fooocus/
├── app/    # fuente oficial Fooocus v2.5.5
└── env/    # entorno CPython 3.10 exclusivo
```

La fuente aprobada queda fijada al tag `v2.5.5` y al commit
`8da1d3ff68942e2d976675939fe72c95746e366e`. Para comprobar una copia existente:

```powershell
git -C .\data\runtime\fooocus\app remote get-url origin
git -C .\data\runtime\fooocus\app describe --tags --exact-match
git -C .\data\runtime\fooocus\app rev-parse HEAD
```

Con Python 3.10.11 instalado, crear el entorno sólo si aún no existe:

```powershell
$fooocusPython = "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
& $fooocusPython --version
& $fooocusPython -m venv .\data\runtime\fooocus\env
```

La instalación siguiente descarga paquetes, pero no checkpoints ni activos de modelos. Debe
ejecutarse desde la raíz del repositorio y únicamente tras revisarla:

```powershell
& .\data\runtime\fooocus\env\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
& .\data\runtime\fooocus\env\Scripts\python.exe -m pip install -r .\requirements-fooocus.txt
```

`requirements-fooocus.txt` reutiliza los pins oficiales de la fuente local y selecciona PyTorch
2.7.1/torchvision 0.22.1 con CUDA 12.8 como primera combinación para la GPU Blackwell. Es una
compatibilidad candidata: debe aprobar el preflight y un smoke real antes de considerarse validada.
No incluye `xformers`, no ejecuta `entry_with_update.py` y no descarga los cuatro activos auxiliares
ni checkpoints.

Comprobar ambos entornos sin activarlos:

```powershell
& .\data\runtime\fooocus\env\Scripts\python.exe -c "import torch, torchvision, gradio; print(torch.__version__, torchvision.__version__, gradio.__version__); print(torch.version.cuda, torch.cuda.is_available())"
& .\.venv\Scripts\python.exe -c "import gradio_client; print(gradio_client.__version__)"
& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py preflight
```

La aplicación siempre se inicia con `.venv`; el supervisor invoca el intérprete secundario cuando
Fooocus lo necesita:

```powershell
& .\.venv\Scripts\python.exe -m aiopenstudio
```

No se debe activar el entorno Fooocus para ejecutar AIOpenStudio ni lanzar Fooocus manualmente. Los
checkpoints y activos auxiliares viven bajo `<MODEL_LIBRARY_ROOT>/fooocus/`, fuera de ambos entornos.
Los cuatro activos requeridos para el arranque offline se aportan explícitamente con:

```powershell
& .\.venv\Scripts\python.exe scripts\model_library.py download `
  image.fooocus-xl-vae-approx `
  image.fooocus-sd15-vae-approx `
  image.fooocus-xl-to-v1-interposer `
  image.fooocus-prompt-expansion
```

El peso de expansión se complementa con siete archivos pequeños de configuración y tokenizer que
ya vienen en la fuente oficial. El supervisor los sincroniza localmente desde la fuente fijada antes
de arrancar; esta operación no accede a Internet.

El inventario pendiente y los runs están en
[docs/fooocus-validation.md](docs/fooocus-validation.md).

### Servicios externos

- Ollama debe instalarse y ejecutarse como servicio local; su URL predeterminada será `http://localhost:11434`.
- Fooocus usa un proceso aislado supervisado y Gradio como transporte local en loopback; la
  aplicación no ejecuta su actualizador ni descargas automáticas.
- Whisper usa faster-whisper/CTranslate2 en un worker local aislado y no descarga modelos desde la UI.
- PostgreSQL es opcional. La aplicación ofrece modo solo SQLite, réplica PostgreSQL y PostgreSQL
  principal con fallback local; siempre puede iniciar sin servidor disponible.

Copiar `.env.example` a `.env` cuando se necesite configuración local. `.env` nunca se versiona.

### Biblioteca compartida de modelos

Los pesos no se duplican por repositorio. El equipo actual usa
`C:\Users\fario\Documents\AIModels` como raíz compartida y conserva en SQLite solo metadatos y
rutas relativas.

Inicializar sin descargar:

```powershell
python scripts/model_library.py init
```

Consultar candidatos y descargar uno de forma explícita:

```powershell
python scripts/model_library.py list
python scripts/model_library.py download llm.phi4-mini-3.8b-q4
```

El script no instala Ollama ni dependencias, no descarga al importar y solicita confirmación de
fuentes/licencias. La estructura, portabilidad y todos los comandos están documentados en
[docs/shared-model-library.md](docs/shared-model-library.md).

### Memoria local y archivos

SQLite guarda referencias de modelos, conversaciones, mensajes y resúmenes. FTS5 proporciona búsqueda textual y `sqlite-vec` queda disponible, pero desactivado hasta elegir el modelo y las dimensiones de embeddings para RAG. La base predeterminada es `data/runtime/memory.sqlite3`.

Los pesos, audios e imágenes no se guardan dentro de SQLite. Sus archivos viven bajo `data/` —ignorado por Git— o en rutas externas configuradas mediante `.env`; la base conserva únicamente rutas y metadatos. Los manifiestos pequeños y versionables permanecen en `models/`.

Las decisiones y sus consecuencias están en
[docs/decisions/local-memory-storage.md](docs/decisions/local-memory-storage.md) y
[docs/decisions/shared-model-library.md](docs/decisions/shared-model-library.md).

### Persistencia PostgreSQL opcional

El extra no instala otro servidor PostgreSQL. Instala dentro de `.venv` únicamente Alembic
(migraciones), psycopg (driver cliente) y keyring (almacén seguro de credenciales), además de
registrar AIOpenStudio en modo editable:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
```

PostgreSQL 18, si ya está instalado, continúa siendo el único servidor. SQLAlchemy 2.x modela y
escribe las entidades; psycopg transporta las consultas y Alembic controla la versión de las tablas.

#### Preparar una base vacía

Se recomienda una base dedicada. Puede crearse con pgAdmin, DBeaver o `psql`. El siguiente comando
abre `psql` y solicita la contraseña de administración de forma interactiva, sin incluirla en la
línea de comandos:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d postgres
```

Dentro de `psql`, reemplazar los nombres y la contraseña por valores propios:

```sql
CREATE ROLE aiopenstudio LOGIN PASSWORD '<contraseña-elegida-manualmente>';
CREATE DATABASE aiopenstudio OWNER aiopenstudio ENCODING 'UTF8';
```

Si ya existe una base vacía y un usuario con permisos para crear tablas, se pueden usar directamente.
AIOpenStudio crea o actualiza sus tablas, índices y `alembic_version`; nunca crea el servidor ni la
base de datos.

#### Configurar desde la aplicación

Iniciar la aplicación:

```powershell
& .\.venv\Scripts\python.exe -m aiopenstudio
```

Los tres modos aparecen directamente en `Configuración`, debajo de
`Conexión PostgreSQL…`. La conexión y sus campos se administran desde ese diálogo:

- Modo de persistencia:
  - `Solo SQLite`: no conecta ni replica hacia PostgreSQL.
  - `SQLite + réplica PostgreSQL`: SQLite es autoritativo y el outbox replica a PostgreSQL.
  - `PostgreSQL principal`: las configuraciones, ejecuciones y metadatos se escriben directamente
    en PostgreSQL mientras esté conectado.
- Servidor: normalmente `127.0.0.1` para PostgreSQL local.
- Puerto: normalmente `5432`.
- Base de datos: por ejemplo `aiopenstudio`.
- Usuario y contraseña del rol elegido.
- SSL/TLS: `prefer` para una instalación local predeterminada; usar `require` cuando el servidor
  esté configurado para exigir TLS.
- Timeout: 5 segundos es el valor inicial.
- `Autocrear o actualizar tablas mediante Alembic`: activar para una base vacía.
- `Guardar contraseña`: usa Windows Credential Manager mediante `WinVaultKeyring`; no escribe el
  secreto en el repositorio.
- `Sincronizar historial local existente`: acción de una sola vez para replicar datos anteriores.

`Probar conexión` verifica autenticación, servidor, base, usuario y revisión Alembic. Con la
autocreación activa también prepara el esquema vacío. `Conectar y guardar` habilita la réplica y
persiste el perfil para reconectar al próximo arranque.

Si el servidor no responde durante un reinicio, la aplicación informa el fallo y continúa usando
SQLite. En modo replicado, SQLite ya contiene la escritura y el outbox queda pendiente. En modo
PostgreSQL principal, se activa un fallback SQLite y sus operaciones también quedan en el outbox
para una recuperación posterior. La preferencia guardada **no cambia automáticamente**: el usuario
debe abrir este diálogo para reconectar o volver manualmente a un modo SQLite. Si PostgreSQL
principal se deshabilita, la aplicación muestra esta advertencia tanto en el diálogo como al próximo
arranque.

El alcance PostgreSQL comprende configuraciones, ejecuciones y metadatos de artefactos. La memoria
conversacional LLM continúa local en SQLite y los binarios permanecen fuera de ambas bases.

El perfil local sin contraseña se guarda bajo `data/runtime/database/`, ignorado por Git. El secreto
se lee desde `AIOPENSTUDIO_DATABASE_PASSWORD` o, si el usuario lo autoriza en el diálogo, desde el
almacén seguro del sistema. Para administrarlo manualmente, copiar `.env.example` a `.env` y añadir
el valor sólo en el archivo ignorado:

```dotenv
AIOPENSTUDIO_DATABASE_PASSWORD=<definir-manualmente>
```

No almacenar contraseñas en `AIOPENSTUDIO_DATABASE_URL`, comandos, logs o archivos versionados.
Consulta [docs/postgres-validation.md](docs/postgres-validation.md) para la validación y
[la decisión arquitectónica](docs/decisions/optional-postgres-replication.md) para las garantías de
sincronización.

## Desarrollo

```powershell
pytest
ruff check .
mypy src
```

Iniciar la aplicación:

```powershell
.\.venv\Scripts\python.exe -m aiopenstudio
```

El tab LLM consulta modelos ya instalados en Ollama, conversa por streaming y ofrece dictado por
micrófono mediante Whisper. Si la VRAM no admite ambos modelos, espera a que el LLM quede ocioso, lo
pasa temporalmente a CPU, transcribe y restaura su residencia GPU.

El tab Whisper abre audio local o graba desde el micrófono, distingue el modelo seleccionado del
modelo realmente residente, cambia de modelo sin exigir una liberación manual, muestra progreso y
segmentos, cancela y exporta TXT, JSON, SRT o VTT. Sus runs seguros y reales están en
[docs/whisper-validation.md](docs/whisper-validation.md).

El tab Fooocus ofrece parámetros, cola FIFO, cancelación y galería. Cada ejecución copia imágenes
verificadas y metadatos a un directorio propio. Antes de usar la GPU espera operaciones activas,
suspende residentes administrados y los restaura al terminar. La configuración y los runs delegados
están en [docs/fooocus-validation.md](docs/fooocus-validation.md).

El tab Monitor muestra CPU, RAM, GPU/VRAM, procesos, residencia por runtime, cola administrada,
tokens de la última inferencia y una lista segura de configuración Ollama. La recolección puede
detenerse y no bloquea el hilo de Tkinter. Sus límites de observabilidad, políticas y batería de
validación están en [docs/resource-monitoring.md](docs/resource-monitoring.md).

La batería segura, los runs con modelo real, sus ETA y reportes están en
[docs/ollama-validation.md](docs/ollama-validation.md). Las limitaciones de residencia y proceso de
Ollama se registran en
[docs/decisions/ollama-runtime-boundary.md](docs/decisions/ollama-runtime-boundary.md).

## Plan

El plan incremental, los criterios de aceptación y la etapa específica de búsqueda de herramientas están en [docs/PLAN.md](docs/PLAN.md).

El inventario real de hardware y servicios, la matriz CUDA/PyTorch, las rutas de datos, los presupuestos de memoria y las amenazas locales están en [docs/environment-compatibility.md](docs/environment-compatibility.md).

## Convención de commits

No se crearán commits sin autorización explícita. Formatos aceptados:

- `feat (scope): description`
- `fix (scope): description`
- `refactor (scope): description`
- `docs (scope): description`

## Licencia

Este repositorio usa la licencia incluida en [LICENSE](LICENSE).
