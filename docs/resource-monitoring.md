# Monitor de recursos

> Estado: aceptado el 17 de agosto de 2026. La validación final tomó 30 muestras en 30,071 segundos
> (intervalo medio de 1,016 segundos), mantuvo disponibles los cuatro proveedores y no produjo
> advertencias. La revisión manual confirmó carga, prompts y liberación con recuperación de VRAM.

El tab **Monitor** reúne telemetría del sistema, NVIDIA, Ollama y futuros adaptadores en proceso sin
importar esos backends desde la UI. El muestreo se ejecuta en el worker asíncrono, no en el hilo de
Tkinter, y se detiene cuando se desmarca `Telemetría activa`.

## Qué puede observar

- CPU y RAM del equipo mediante `psutil`.
- GPU, VRAM, utilización, temperatura, potencia y memoria por proceso mediante NVML cuando existe
  una GPU NVIDIA compatible.
- modelos residentes, memoria total, reparto RAM/VRAM, contexto y expiración que Ollama publica en
  `/api/ps`;
- tokens de entrada y salida, duración y tokens por segundo de la última inferencia terminada que
  atraviesa `LLMService`;
- modelos que AIOpenStudio está cargando, su tamaño esperado cuando existe en el catálogo y el
  dispositivo solicitado;
- un registro genérico para que futuros adaptadores PyTorch/Hugging Face publiquen pesos, KV cache,
  activaciones y memoria reservada sin acoplar el servicio a PyTorch.

Cada asignación declara su calidad como `measured`, `runtime_reported`, `derived`, `estimated` o
`unknown`. Ollama sólo publica el total residente y la parte situada en VRAM: no permite separar de
forma fiable pesos, KV cache, activaciones y overhead. Por eso el gráfico etiqueta esa cantidad como
`runtime_other`; no inventa un desglose. El tamaño de activaciones sólo aparecerá cuando un adaptador
en proceso lo mida y lo registre explícitamente.

El mapa apilado excluye las asignaciones de proceso de NVML cuando ya existe atribución por modelo,
para no sumar dos veces la misma VRAM. La tabla de procesos conserva ambas vistas para diagnóstico.

## Controles y políticas

- **Telemetría activa** detiene/reanuda la recolección; no sólo oculta la gráfica.
- **Muestra ahora** solicita una fotografía adicional fuera del ciclo periódico.
- **Liberar selección** permite descargar un modelo. Si no fue cargado por AIOpenStudio se solicita
  confirmación porque podría pertenecer a otro cliente de Ollama.
- **Liberar inactivos** afecta únicamente a modelos administrados por esta aplicación y respeta el
  tiempo de inactividad.
- **Liberación automática** está desactivada por defecto y nunca toma propiedad automática de un
  modelo encontrado externamente.

Los límites duros bloquean una nueva carga administrada; los límites blandos generan advertencias.
`MONITORING_MAX_MANAGED_MODELS` es una política de AIOpenStudio y no sustituye la programación
interna de Ollama.

El panel muestra una lista segura y de sólo lectura de variables Ollama relevantes:
`OLLAMA_CONTEXT_LENGTH`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_QUEUE`,
`OLLAMA_MODELS`, `OLLAMA_HOST` y `OLLAMA_NO_CLOUD`. Modificarlas fuera del proceso normalmente exige
reiniciar Ollama; la aplicación no edita silenciosamente el entorno del usuario.

## Configuración

Las variables se encuentran en `.env.example` y usan el prefijo `AIOPENSTUDIO_`. Valores iniciales:

```dotenv
AIOPENSTUDIO_MONITORING_ENABLED=true
AIOPENSTUDIO_MONITORING_INTERVAL_SECONDS=1.0
AIOPENSTUDIO_MONITORING_HISTORY_SAMPLES=120
AIOPENSTUDIO_MONITORING_AUTO_RELEASE_ENABLED=false
AIOPENSTUDIO_MONITORING_IDLE_TIMEOUT_SECONDS=600
AIOPENSTUDIO_MONITORING_MAX_MANAGED_MODELS=1
AIOPENSTUDIO_MONITORING_RAM_SOFT_LIMIT=0.85
AIOPENSTUDIO_MONITORING_RAM_HARD_LIMIT=0.92
AIOPENSTUDIO_MONITORING_VRAM_SOFT_LIMIT=0.80
AIOPENSTUDIO_MONITORING_VRAM_HARD_LIMIT=0.90
```

El intervalo mínimo aceptado es 0,5 segundos. Un intervalo de un segundo ofrece una visualización
fluida con bajo costo; el modo diagnóstico queda reservado para instrumentación posterior.

## Validación delegada

La batería segura no descarga ni carga modelos.

```powershell
\.venv\Scripts\python.exe -m pytest -q
\.venv\Scripts\python.exe -m ruff check .
\.venv\Scripts\python.exe -m mypy src
```

ETA local: 1–10 segundos por comando. ETA global: 5–30 segundos. La salida queda en consola.

El preflight toma una muestra y comprueba degradación sin NVIDIA/Ollama:

```powershell
\.venv\Scripts\python.exe scripts\validate_resource_monitor.py preflight
```

ETA local/global: 1–10 segundos; no genera reporte.

La observación predeterminada toma 30 muestras durante unos 30–40 segundos y genera exactamente un
JSON en `data/outputs/resource-monitor-validation/`:

```powershell
\.venv\Scripts\python.exe scripts\validate_resource_monitor.py observe
```

Para verificar atribución mientras un modelo ya está residente, cárgalo manualmente desde el tab
LLM y ejecuta el mismo comando. El script sólo observa; no ejecuta inferencias ni cambia residencia.
Cada muestra conserva estados, asignaciones y advertencias. El reporte no contiene conversaciones,
prompts, secretos ni respuestas.

Finalmente, iniciar la app y comprobar durante 3–8 minutos:

```powershell
\.venv\Scripts\python.exe -m aiopenstudio
```

Verificar actualización de gráficas sin congelamiento, pausa/reanudación, degradación visible de un
proveedor ausente, tokens tras un chat, residencia al cargar/liberar y confirmación para modelos
externos.
