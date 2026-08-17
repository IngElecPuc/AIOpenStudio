# AIOpenStudio

AIOpenStudio será una aplicación de escritorio en Python para descubrir, ejecutar y administrar modelos de inteligencia artificial locales. La interfaz se construirá con Tkinter y tendrá suites independientes para LLM, Fooocus y Whisper. Ollama será el primer backend de la suite de LLM.

> Estado: base arquitectónica y contratos iniciales. La UI y las integraciones con modelos aún no están implementadas.

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
| Whisper | Whisper | Transcripción y procesamiento de audio |

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
```

Tkinter forma parte de la instalación estándar de Python en Windows y no se instala con `pip`.

### PyTorch y GPU

El inventario confirmó una RTX 5060 Laptop, compute capability 12.0, driver 573.13 y CUDA Toolkit 12.8. `requirements.txt` selecciona la combinación oficial PyTorch 2.11.0, torchvision 0.26.0 y torchaudio 2.11.0 con wheels CUDA 12.8. Esa variante ya contiene soporte GPU; no existe otro paquete separado llamado `pytorch-gpu`.

Verificar después de instalar:

```powershell
python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'); print(torch.cuda.get_device_capability(0) if torch.cuda.is_available() else 'N/A')"
```

El monitoreo NVIDIA usará NVML cuando esté disponible. La arquitectura no asumirá NVIDIA de forma permanente: el contrato de métricas admite otros proveedores.

### Servicios externos

- Ollama debe instalarse y ejecutarse como servicio local; su URL predeterminada será `http://localhost:11434`.
- Fooocus se integrará mediante un adaptador de proceso/API, sin copiar su código dentro del núcleo.
- Whisper tendrá un adaptador local de PyTorch; la variante concreta se seleccionará durante la fase de investigación.
- PostgreSQL será opcional. La aplicación deberá iniciar sin una base de datos configurada.

Copiar `.env.example` a `.env` cuando se necesite configuración local. `.env` nunca se versiona.

## Desarrollo

```powershell
pytest
ruff check .
mypy src
```

Todavía no existe un entry point funcional: se añadirá con la primera vertical completa de la suite LLM.

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
