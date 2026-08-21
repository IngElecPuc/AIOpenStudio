# Validación de robustez

## Matriz segura automatizada

La batería cubre exclusión de leases GPU concurrentes, cancelación de un waiter, rechazo previo
por presión sintética de RAM/VRAM, cancelaciones terminales, reinicios acotados, reconciliación de
ejecuciones y cierre que continúa después de un timeout.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests\unit\test_robustness_matrix.py `
  tests\unit\test_lifecycle.py `
  tests\unit\test_runtime_recovery.py
```

Estas pruebas no cargan modelos, no reservan memoria real y no provocan OOM.

## Matriz manual pendiente

1. Exportar Diagnósticos con todas las suites inactivas.
2. Cerrar la aplicación durante LLM streaming y comprobar terminal/cierre de cliente.
3. Cerrar durante una transcripción y comprobar fin del worker/micrófono.
4. Cerrar durante Fooocus en cola, carga y sampler; comprobar árbol de procesos y puerto.
5. Ejecutar intercambio real LLM → Whisper → Fooocus y confirmar restauración de GPU.
6. Detener manualmente un worker propiedad de AIOpenStudio y comprobar un reinicio acotado.
7. Revisar JSONL, ZIP y persistencia para confirmar ausencia de contenido sensible.

Un OOM deliberado no forma parte de esta matriz segura y requiere autorización explícita, memoria
inicial registrada, un único runtime pesado y un criterio de terminación acordado.
