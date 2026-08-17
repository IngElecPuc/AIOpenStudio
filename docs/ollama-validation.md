# Validación de la vertical LLM

La batería separa comprobaciones rápidas de ejecuciones que pueden cargar un modelo. Ningún comando
descarga modelos: si el nombre solicitado no aparece en el catálogo de Ollama, la ejecución falla
antes de inferir.

## Resultado de aceptación

La vertical fue aceptada el 17 de agosto de 2026 con `phi4-mini:3.8b-q4_K_M`:

- 28 pruebas unitarias aprobadas, Ruff y Mypy sin hallazgos;
- preflight aprobado contra Ollama 0.32.13 y 11 modelos instalados;
- smoke test aprobado en 6,65 segundos, con carga en GPU y término `completed`;
- cancelación aprobada en 6,48 segundos, con evento `cancelled` a los 2 segundos;
- ambos runs terminaron con RAM y GPU en estado `unloaded`;
- tab LLM validado manualmente para catálogo, carga, streaming, cancelación, liberación y cierre.

Los reportes permanecen como artefactos locales ignorados por Git en
`data/outputs/ollama-validation/`.

## Orden recomendado

### Calidad local

Estos comandos no contactan Ollama ni cargan pesos. Cada uno usa sólo salida de consola.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
```

ETA local: 1–10 segundos por comando. ETA global: 5–30 segundos.

### Preflight de Ollama

Consulta salud y catálogo, pero no carga modelos. También usa sólo salida de consola.

```powershell
.\.venv\Scripts\python.exe scripts\validate_ollama_vertical.py preflight
```

Para comprobar además un nombre exacto:

```powershell
.\.venv\Scripts\python.exe scripts\validate_ollama_vertical.py preflight --model phi4-mini:3.8b-q4_K_M
```

ETA local y global: 1–5 segundos.

La prueba de integración optativa cubre la misma frontera desde Pytest:

```powershell
$env:AIOPENSTUDIO_RUN_OLLAMA_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest -q -m ollama_integration
Remove-Item Env:AIOPENSTUDIO_RUN_OLLAMA_INTEGRATION
```

ETA local y global: 1–10 segundos. No genera reporte porque es una consulta breve.

### Smoke test con generación

Este run carga un modelo instalado, genera hasta 64 tokens y lo libera al terminar:

```powershell
.\.venv\Scripts\python.exe scripts\validate_ollama_vertical.py smoke --model phi4-mini:3.8b-q4_K_M
```

El script imprime sus ETA antes de empezar. Para modelos de hasta unos 3,5 GB estima carga en
15–90 segundos, primer token en 5–60 segundos, generación en 15–120 segundos y liberación en
2–30 segundos; ETA global de 1–5 minutos. Para modelos mayores estima 2–7 minutos globales.

### Cancelación real

El escenario usa por defecto un prompt deliberadamente largo y solicita cancelar después de cinco
segundos. Puede ajustarse sin cambiar el código:

```powershell
.\.venv\Scripts\python.exe scripts\validate_ollama_vertical.py cancel --model phi4-mini:3.8b-q4_K_M --cancel-after 2 --max-new-tokens 512
```

ETA local: catálogo 1–5 segundos, carga 15–180 segundos, cancelación 5–20 segundos y liberación
2–30 segundos. ETA global: 1–7 minutos según el tamaño del modelo.

## Reportes

Cada invocación `smoke` o `cancel` crea exactamente un JSON nuevo en
`data/outputs/ollama-validation/`. El nombre incluye escenario, fecha UTC y UUID; esa carpeta queda
fuera de Git. El reporte contiene:

- ETA prevista y duración real de cada paso;
- descriptor del modelo y estados de RAM/GPU reportados por Ollama;
- secuencia de eventos y métricas de tokens/duración;
- hash y longitud del prompt, no el prompt completo;
- hasta 2.000 caracteres de respuesta para diagnóstico;
- error y resultado final cuando algo falla.

Si un run falla, compartir el único JSON producido junto con la salida de consola. `preflight` no
escribe reportes. El timeout global predeterminado es 600 segundos y puede cambiarse con
`--timeout`.

## Revisión manual de la UI

Sólo después de aprobar `smoke` y `cancel`:

```powershell
.\.venv\Scripts\python.exe -m aiopenstudio
```

ETA global de revisión: 3–8 minutos, sin incluir la primera carga del modelo. Verificar que:

- el tab LLM lista únicamente modelos instalados;
- `Cargar` cambia los estados visibles sin congelar la ventana;
- el texto aparece por streaming y `Cancelar` devuelve el control;
- `Liberar` deja RAM/GPU como `unloaded` según `/api/ps`;
- cerrar la ventana termina el worker asíncrono sin traceback.

Los tabs Whisper y Fooocus son marcadores visibles y no ejecutan backends todavía.
