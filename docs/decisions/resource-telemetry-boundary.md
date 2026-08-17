# Decisión: frontera de telemetría y calidad de medición

- Estado: aceptada para implementación; validación local pendiente.
- Fecha: 2026-08-17.

## Contexto

La aplicación combinará runtimes externos como Ollama con modelos alojados en el propio proceso por
PyTorch/Hugging Face. Ninguna fuente expone el mismo nivel de detalle y atribuir toda la memoria a
pesos, KV cache o activaciones a partir de un total produciría datos engañosos.

## Decisión

`core.contracts.monitoring` define datos neutrales y una calidad obligatoria para cada asignación.
Proveedores concretos viven en `infrastructure.monitoring`; el agregador, historial y políticas
viven en `services.resource_monitor`; Tkinter consume sólo el servicio.

NVML y psutil aportan medidas físicas/proceso. Ollama aporta totales del runtime. Los adaptadores en
proceso deben registrar explícitamente sus mediciones en `InProcessTelemetryRegistry`. La ausencia o
el fallo de un proveedor degrada sólo ese proveedor y no bloquea la aplicación.

La liberación automática sólo puede operar sobre modelos cuya carga atravesó el servicio y está
desactivada por defecto. Los modelos detectados externamente pueden liberarse únicamente mediante
una acción manual confirmada.

## Consecuencias

- La UI puede comparar fuentes heterogéneas sin importar SDKs.
- Los valores no observables permanecen como desconocidos o agregados; no existe falsa precisión.
- Fooocus, Whisper y futuros runtimes pueden añadir proveedores sin cambiar el contrato visual.
- Medir activaciones con precisión requerirá instrumentación del adaptador PyTorch y tendrá un modo
  diagnóstico separado por su costo potencial.
