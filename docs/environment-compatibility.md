# Fase 0 — Entorno y matriz de compatibilidad

Inventario realizado el **16 de agosto de 2026** en el equipo de desarrollo. Los valores de memoria libre, temperatura y utilización son una fotografía del momento; deben volver a medirse antes de cargar un modelo.

## Resumen ejecutivo

El equipo es apto para inferencia local acelerada con PyTorch y CUDA 12.8, con una restricción práctica de aproximadamente 8 GiB de VRAM. Python 3.12 x64 es el runtime elegido. Python 3.14 también está instalado, pero queda fuera del entorno del proyecto para reducir incompatibilidades con runtimes de modelos.

Ollama 0.32.14 está instalado y respondió a la validación local. PostgreSQL 18 está activo;
actualmente escucha en todas las interfaces en el puerto 5432 y requiere una revisión de exposición
antes de usarlo desde la aplicación.

## Matriz de compatibilidad

| Componente | Descubrimiento | Estado para AIOpenStudio | Decisión inicial |
|---|---|---|---|
| Sistema operativo | Windows 11 Home Single Language, x64, versión 10.0.26200 | Compatible | Soportar Windows como plataforma primaria |
| CPU | Intel Core i9-13900H, 14 núcleos y 20 procesadores lógicos | Compatible | Adecuado para coordinación y fallback CPU; no asumir que CPU igualará el rendimiento GPU |
| RAM | 32 GiB instalados; 31,7 GiB visibles; 8,3 GiB libres durante el inventario | Compatible con presión actual alta | Medir memoria libre antes de cada carga y conservar una reserva del sistema |
| GPU dedicada | NVIDIA GeForce RTX 5060 Laptop GPU | Compatible | GPU primaria para PyTorch y modelos locales |
| VRAM | 8.151 MiB totales; 593 MiB usados y 7.218 MiB libres durante la muestra | Limitada para concurrencia pesada | Una carga pesada GPU a la vez inicialmente |
| Compute capability | 12.0 | Compatible con builds CUDA 12.8 seleccionados | Usar wheels oficiales `cu128`; verificar arquitecturas incluidas tras instalar |
| Driver NVIDIA | 573.13 | Compatible con CUDA 12.8 | No cambiarlo para la fase inicial |
| CUDA reportada por driver | 12.8 | Compatible | No confundir esta cifra con la versión embebida en cada wheel |
| CUDA Toolkit local | 12.8, `nvcc` 12.8.93 | Compatible; no requerido para wheels precompilados | Usarlo solo si una dependencia necesita compilar extensiones |
| GPU integrada | Intel Iris Xe Graphics | No elegida para PyTorch CUDA | Mantenerla fuera del runtime CUDA inicial |
| Python 3.12 | CPython 3.12.10, Windows x64 | Recomendado | Crear `.venv` exclusivamente con 3.12 |
| Python 3.14 | CPython 3.14.6 | No seleccionado | No usar en el entorno del proyecto por ahora |
| PyTorch | 2.11.0+cu128 instalado junto con torchvision 0.26.0 y torchaudio 2.11.0 | Compatible y validado: CUDA disponible, RTX 5060 detectada y capability 12.0 | Mantener la integración desacoplada y medir cada modelo antes de admitirlo |
| SQLite | 3.49.1 incluido con Python 3.12; FTS5 habilitado | Compatible y validado | Base local para referencias, conversaciones y resúmenes |
| sqlite-vec | 0.1.9, extensión cargable validada | Compatible, opcional y pre-1.0 | Desactivado hasta definir embeddings y dimensiones |
| Ollama runtime | 0.32.14 accesible localmente | Compatible y validado | Mantener bind local y no descargar modelos implícitamente |
| Cliente Python Ollama | Declarado en `requirements.txt` | Compatible | El paquete Python no instala el runtime Ollama |
| Fooocus | No inventariado/instalado | Pendiente de Fase 2 | Mantener un entorno de runtime aislado para evitar conflictos de PyTorch |
| Whisper | Snapshots `small`, `medium` y `large-v3`; faster-whisper 1.2.1 y CTranslate2 4.8.1 | CPU `small` validado dos veces | Validar GPU, micrófono, cancelación, cambio a `medium` y OOM |
| FFmpeg | No detectado en `PATH` | No bloquea faster-whisper/PyAV | Instalar sólo si otro backend o flujo externo lo requiere |
| PostgreSQL | PostgreSQL Server 18 activo, inicio automático | Disponible con riesgo de exposición | Integración opcional; revisar bind, firewall y `pg_hba.conf` |
| Almacenamiento | Unidad C: 952,4 GiB totales y 687,9 GiB libres | Suficiente | Aplicar cuotas y no versionar pesos ni outputs |

### Compatibilidad CUDA

NVIDIA establece que CUDA 12.x en Windows requiere al menos un driver 528.33 para compatibilidad menor; CUDA 12.8 GA requiere una rama 570 o posterior. El driver 573.13 observado supera ambos umbrales. PyTorch publica wheels de Windows/Python 3.12 para CUDA 12.8 y documenta la combinación 2.11.0/0.26.0/2.11.0.

El toolkit CUDA instalado globalmente no sustituye al runtime CUDA empacado en los wheels de PyTorch. La prueba definitiva, después de crear el entorno, será comprobar `torch.version.cuda`, `torch.cuda.is_available()`, nombre del dispositivo y capability.

## Ubicaciones de datos

Todas las rutas de aplicación son configurables. Los valores relativos se resuelven desde la raíz del repositorio durante desarrollo.

| Recurso | Ruta predeterminada | Política |
|---|---|---|
| Manifiestos pequeños | `models/<suite>/` | Versionados; nunca incluir pesos |
| Entradas del usuario | `data/inputs/` | Locales, ignoradas por Git, conservación explícita |
| Resultados | `data/outputs/` | Locales, ignorados por Git, separados por ejecución |
| Caché Hugging Face | `data/cache/huggingface/` | Ignorada por Git; configurar mediante `HF_HOME` |
| Estado temporal | `data/runtime/` | Ignorado por Git; se puede limpiar con la app cerrada |
| Logs | `data/logs/` | Ignorados por Git; no registrar prompts ni tokens por defecto |
| Biblioteca compartida | `C:\Users\fario\Documents\AIModels` | Externa al repositorio; raíz configurada por `.env` |
| Catálogo compartido | `catalog/model-library.sqlite3` bajo la biblioteca | Solo metadatos y rutas relativas; no contiene pesos |
| Pesos Ollama | `ollama/` bajo la biblioteca | Estructura administrada por Ollama mediante `OLLAMA_MODELS` |
| Caché Hugging Face | `huggingface/` bajo la biblioteca | Compartida mediante `HF_HOME`; revisiones resueltas antes de registrar |
| Whisper, Fooocus y embeddings | `whisper/`, `fooocus/` y `embeddings/` | Pesos reutilizables por varios proyectos; licencias por artefacto |
| Base local | `data/runtime/memory.sqlite3` | Referencias, conversaciones, resúmenes e índices; nunca pesos ni multimedia |
| Base externa | PostgreSQL externo/opcional | Uso futuro para escenarios compartidos; no guardar binarios grandes |

Fooocus deberá ejecutarse con su propio entorno y directorio bajo `data/runtime/fooocus/`. No debe compartir automáticamente el entorno Python principal porque sus restricciones de PyTorch pueden diferir.

### Entornos Python de respaldo

El entorno activo es `.venv`. Existen dos respaldos descartables, `.venv-broken-20260820-104516`
(aproximadamente 4,93 GB) y `venv-py314-backup` (aproximadamente 171 MB). No contienen datos de la
aplicación ni pesos y no deben conservarse indefinidamente. Se pueden eliminar con la aplicación
cerrada cuando `.venv` haya aprobado CPU, GPU y micrófono y se haya confirmado que no guardan una
configuración única. Su eliminación requiere una autorización explícita separada.

## Presupuestos y política de residencia

Estos límites son valores iniciales y configurables. Se ajustarán con telemetría real de cada modelo.

### GPU/VRAM

- Reservar al menos **1,25 GiB** de VRAM libre para Windows, UI, contexto y variaciones de inferencia.
- Limitar inicialmente un modelo a **5,5 GiB estimados** de VRAM y ejecutar solo una carga pesada GPU a la vez.
- Umbral suave: 80 % de VRAM total. No admitir otra carga; ofrecer liberar el modelo menos reciente que no esté fijado.
- Umbral crítico: 90 %. Cancelar nuevas admisiones y liberar cargas inactivas no fijadas.
- Un modelo marcado como “mantener” queda exento del temporizador de inactividad. Si solo quedan modelos fijados, bloquear la nueva tarea y pedir una decisión al usuario.
- Medir también caché KV, tensores temporales y proceso del runtime; el tamaño del archivo de pesos no equivale al consumo final.

### RAM

- Mantener una reserva mínima de **6 GiB libres** para Windows y aplicaciones del usuario.
- Presupuesto suave de AIOpenStudio: **12 GiB**; presupuesto máximo inicial: **16 GiB**.
- No cargar un modelo si su estimación deja menos de 6 GiB libres.
- Con uso total del sistema sobre 85 %, liberar modelos inactivos no fijados y detener nuevas admisiones.
- La muestra inicial solo tenía 8,3 GiB libres: en esa condición se deben cerrar otras aplicaciones o usar modelos pequeños antes de cargar pesos grandes en RAM.

### Reglas comunes

- Tiempo de inactividad inicial: 10 minutos para modelos no fijados.
- Liberar significa descargar de RAM/VRAM mediante el runtime; nunca eliminar archivos.
- No descargar modelos desde Internet, actualizarlos ni eliminarlos sin una acción explícita del usuario.
- El monitor debe tomar una muestra justo antes y después de cada transición de residencia.
- La estimación desconocida se trata como no admisible hasta que el usuario confirme o exista una medición previa.

## Amenazas locales y controles

| Riesgo | Observación | Control requerido |
|---|---|---|
| Secretos | Tokens de Hugging Face y credenciales PostgreSQL pueden aparecer en entorno o logs | Usar `.env` ignorado; redactar variables con `TOKEN`, `KEY`, `SECRET` o contraseñas; nunca mostrar connection strings completas |
| Contenido privado | Prompts, audio, transcripciones e imágenes pueden contener datos sensibles | Persistencia de contenido desactivada por defecto; borrado explícito y retención configurable |
| Código remoto de modelos | Algunos repositorios solicitan `trust_remote_code=True` | Valor predeterminado `False`; requerir aprobación y registrar revisión antes de habilitarlo |
| Formatos inseguros | Pickle y algunos checkpoints pueden ejecutar código al cargar | Preferir `safetensors`; verificar origen, licencia y checksum |
| Ollama expuesto en red | No está instalado actualmente | Al instalar, enlazar a loopback; no usar `0.0.0.0:11434` sin autenticación/proxy y aprobación |
| PostgreSQL expuesto | Se observó `0.0.0.0:5432` y `[::]:5432` | Revisar firewall, `listen_addresses`, TLS y `pg_hba.conf` antes de conectar AIOpenStudio |
| Procesos externos | Fooocus/Ollama pueden heredar permisos del usuario | Ejecutar sin elevación, con argumentos estructurados, límites y apagado supervisado |
| Agotamiento de recursos | Varias suites pueden competir por VRAM/RAM/disco | Gestor central de admisión, una carga GPU pesada inicial, cuotas y cancelación |
| Cadena de suministro | Modelos y paquetes descargables pueden cambiar | Fijar versiones aprobadas, registrar fuente/licencia/hash y evitar instalaciones desde ramas arbitrarias |
| Datos versionados | Pesos y outputs pueden entrar accidentalmente a Git | `.gitignore` verificado para `.env`, `data/`, pesos, `outputs/` y `cache/` |

## Comprobaciones posteriores a la instalación

```powershell
python --version
python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print(torch.cuda.get_device_capability(0))"
python -c "import transformers, huggingface_hub, ollama; print(transformers.__version__, huggingface_hub.__version__, ollama.__version__)"
ollama --version
ollama ps
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

Estas comprobaciones no deben descargar un modelo. Una prueba de inferencia se realizará después con un modelo aprobado y ya inventariado.

## Pendientes de cierre

1. Confirmar periódicamente que Ollama permanezca ligado localmente; la ubicación objetivo está
   fijada en la biblioteca compartida mediante `OLLAMA_MODELS`.
2. Completar los runs Whisper GPU, cancelación, micrófono, cambio a `medium` y presión de memoria;
   FFmpeg no es requisito de faster-whisper porque PyAV incluye sus bibliotecas.
3. Revisar la exposición de PostgreSQL 18 antes de configurar credenciales en la aplicación.
4. Validar los presupuestos con un modelo pequeño, uno mediano y una transcripción de prueba.

## Fuentes técnicas

- [PyTorch — Previous versions](https://pytorch.org/get-started/previous-versions/)
- [PyTorch — Windows and local installation](https://docs.pytorch.org/get-started/locally/)
- [NVIDIA — CUDA 12.8 release notes](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html)
- [Hugging Face Transformers — instalación](https://huggingface.co/docs/transformers/installation)
- [Hugging Face Hub — instalación](https://huggingface.co/docs/huggingface_hub/en/installation)
- [Ollama Python — cliente oficial](https://github.com/ollama/ollama-python)
