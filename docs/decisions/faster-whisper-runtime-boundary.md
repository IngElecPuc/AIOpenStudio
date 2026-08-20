# Decisión: frontera local de faster-whisper y dictado para LLM

- Estado: implementada; validación nativa CPU/GPU pendiente.
- Fecha: 2026-08-20.

## Contexto

Whisper debe operar desde su propio tab y prestar dictado al compositor LLM. CTranslate2 usa
bibliotecas nativas CUDA distintas de PyTorch y una inferencia bloqueante no ofrece cancelación
fuerte desde el hilo que la invoca. La aplicación debe seguir funcionando si Whisper, el micrófono
o una GPU compatible no están disponibles.

## Decisión

- Usar `faster-whisper` como backend inicial y abrir exclusivamente snapshots locales de la
  biblioteca compartida; nunca pasar nombres remotos al constructor.
- Alojar CTranslate2 y el modelo en un worker descartable iniciado mediante `spawn` en Windows.
- Mantener Tkinter como cliente principal de `TranscriptionService`; una API futura deberá reutilizar
  ese servicio y no invocar el adaptador directamente.
- Usar cancelación cooperativa entre segmentos y terminar el worker si no responde dentro del plazo.
- Terminar el worker para una liberación completa; el offload parcial conserva el modelo en CPU.
- Integrar el PID, residencia y consumo de proceso con el monitor sin inventar un desglose entre
  pesos, activaciones y memoria reservada.
- Para dictado LLM, esperar a que termine la generación activa. Si la admisión indica competencia,
  pasar el modelo Ollama a CPU, ejecutar Whisper en GPU, liberar Whisper y restaurar el LLM en GPU.
  El tab Whisper independiente no desplaza automáticamente otros modelos.
- Capturar micrófono mediante la dependencia opcional `sounddevice`, guardar WAV temporal y
  eliminarlo tras la transcripción.

## Consecuencias

- Un crash nativo, OOM o cancelación forzada no derriba la UI.
- La primera carga tiene el costo de crear un proceso, pero libera VRAM de forma determinista.
- Ollama debe admitir `num_gpu=0` y restauración GPU en la versión local; esto requiere un run real.
- CPU usa `int8`; GPU intenta `int8_float16`. La RTX 5060 sólo se declara compatible después del
  preflight y smoke test reales de CTranslate2.
- La ausencia de dependencias opcionales degrada el control correspondiente sin bloquear el arranque.
