# Decisión: frontera supervisada para Fooocus

- Estado: implementada; validación nativa pendiente.
- Fecha: 2026-08-20.

## Contexto

La aplicación es de escritorio y la UI propia es el cliente principal. Fooocus mantiene una UI
Gradio y no publica una REST estable; además, su stack de PyTorch puede diferir del entorno principal
y su primer arranque puede intentar descargar activos. La RTX 5060 Laptop dispone de unos 8 GiB de
VRAM, insuficientes para asumir convivencia segura con un LLM o Whisper residentes.

## Decisión

- Ejecutar una instalación Fooocus aportada por el usuario en un entorno Python independiente.
- Supervisar `launch.py` como árbol de procesos administrado, ligado sólo a loopback y con modo
  offline. AIOpenStudio no clona, actualiza ni descarga pesos.
- Usar Gradio únicamente como transporte. El adaptador descubre componentes y dependencias por sus
  etiquetas publicadas, sin fijar índices de función propios de una versión.
- Mantener contratos neutrales de generación, cola, progreso, cancelación y artefactos en `core`.
  Una API futura deberá reutilizar `ImageGenerationService`.
- Serializar las generaciones en una cola FIFO. La cancelación solicita primero detener el job y,
  si no responde dentro de la gracia, termina el proceso supervisado.
- Copiar únicamente imágenes verificadas desde raíces temporales permitidas a
  `data/outputs/fooocus/<operation_id>/images/`. Escribir `metadata.json` y `events.jsonl` de forma
  separada por ejecución.
- Adquirir una exclusión global antes de cargar Fooocus. Esperar operaciones LLM/Whisper activas,
  suspender residentes administrados, ejecutar Fooocus en GPU, descargarlo y restaurar las suites.
- Integrar PID, RAM, residencia y estado del runtime con el monitor; NVML sigue siendo la fuente de
  uso físico de VRAM.

## Consecuencias

- Fooocus ausente o incompatible deshabilita sólo su tab y no bloquea el resto de la aplicación.
- La terminación fuerte pierde el proceso y sus caches, pero ofrece cancelación determinista y
  recuperación ante OOM o bloqueos nativos.
- Los cambios de esquema Gradio pueden exigir ajustar el descubrimiento; las pruebas de transporte
  cubren el esquema esperado sin iniciar Fooocus.
- La compatibilidad real de Fooocus/PyTorch con Blackwell no se declara hasta completar los runs
  delegados. No se promete CPU en esta primera vertical.
