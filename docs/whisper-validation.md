# Validación de la suite Whisper

> Estado: preflight y dos runs CPU con `small` aprobados el 20 de agosto de 2026. El entorno contiene
> `faster-whisper 1.2.1`, `ctranslate2 4.8.1`, `av 16.1.0` y `sounddevice 0.5.6`. Siguen pendientes
> GPU, cancelación real, micrófono y escenarios de presión de memoria.

Ningún comando de esta batería descarga modelos. Los snapshots `small`, `medium` y `large-v3` ya
existen bajo la raíz local configurada, por ejemplo `data\models\whisper\Systran`.

## Calidad segura

```powershell
pytest
ruff check .
mypy src
```

ETA global habitual: 5–30 segundos. No genera un reporte de modelo.

## Preflight

```powershell
python scripts/validate_whisper_vertical.py preflight
```

Comprueba paquetes y modelos locales sin cargar pesos. ETA: 1–10 segundos; sólo consola.

## Runs reales delegados

Usar un audio local corto y no sensible. El script no copia su contenido ni incluye texto en el
reporte; registra hash, tamaño, conteos, versiones y tiempos.

```powershell
python scripts/validate_whisper_vertical.py cpu --model small --source C:\ruta\audio.wav
python scripts/validate_whisper_vertical.py gpu --model small --source C:\ruta\audio.wav
python scripts/validate_whisper_vertical.py cancel --model small --source C:\ruta\audio-largo.wav
```

ETA de carga: 15–180 segundos. La transcripción puede tomar entre 0,2 y 3 veces la duración del
audio según dispositivo y modelo. Cada invocación real crea exactamente un JSON en
`data/outputs/whisper-validation/` y libera el worker al terminar.

Después de aprobar `small`, repetir CPU/GPU con `--model medium`. `large-v3`, batch, diarización y
WhisperX quedan fuera del criterio de salida inicial.

## Resultados aceptados

Los dos reportes CPU de `small` terminaron en `completed` sin conservar texto:

- `prueba1.wav`: 2.749.592 bytes, 5 segmentos, 172 caracteres y 3,293 s de ejecución global.
- `prueba2.wav`: 13.030.552 bytes, 6 segmentos, 476 caracteres y 7,501 s de ejecución global.

Los reportes futuros incluyen además duración del audio, tiempo informado por el backend, factor de
tiempo real e idioma detectado. Los reportes anteriores siguen siendo válidos aunque no contengan
esos campos.

## Revisión manual

1. Verificar que el tab distinga siempre modelo seleccionado y modelo residente.
2. Cambiar de `small` a `medium` sin liberar manualmente y confirmar el reemplazo automático.
3. Confirmar progreso incremental, cancelación y exportación TXT, JSON, SRT y VTT.
4. Grabar y transcribir desde el micrófono en el tab Whisper.
5. Dictar desde el botón de micrófono del compositor LLM y comprobar que el texto quede editable.
6. Con un LLM residente en GPU, comprobar que una generación activa no sea interrumpida.
7. Cuando el monitor exija intercambio, comprobar LLM en CPU, Whisper en GPU, liberación de Whisper
   y restauración posterior del LLM en GPU.
8. Confirmar que cerrar la aplicación termina el worker y recupera RAM/VRAM.
