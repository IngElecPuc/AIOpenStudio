# Validación de la suite Whisper

> Estado: preflight y dos runs CPU con `small` aprobados el 20 de agosto de 2026. El entorno contiene
> `faster-whisper 1.2.1`, `ctranslate2 4.8.1`, `av 16.1.0` y `sounddevice 0.5.6`. Siguen pendientes
> GPU, cancelación real, micrófono y escenarios de presión de memoria.

Los contratos avanzados, su traducción al adaptador, la cola secuencial, cancelación en espera,
intervalos, correcciones, exportaciones y dictado experimental están cubiertos por pruebas
unitarias. PyAV se prueba con un WAV sintético y existe un smoke test seguro del tab Tkinter. La
lista de idiomas y la diferencia entre entrada, transcripción y traducción se documentan en
[Idiomas y tareas de Whisper](whisper-language-support.md), y los procedimientos completos en la
[Guía de Whisper](whisper-user-guide.md). Esto no constituye todavía una validación real de
traducción, timestamps por palabra, VAD, hotwords, intervalos o dictado fragmentado.

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
python scripts/validate_whisper_vertical.py translate --model small --language es --source C:\ruta\audio-es.wav
python scripts/validate_whisper_vertical.py word-timestamps --model small --source C:\ruta\audio.wav
python scripts/validate_whisper_vertical.py vad --model small --source C:\ruta\audio.wav
python scripts/validate_whisper_vertical.py hotwords --model small --hotwords "AIOpenStudio,CTranslate2" --source C:\ruta\audio.wav
python scripts/validate_whisper_vertical.py intervals --model small --source C:\ruta\audio.wav --interval 0-10 --interval 20-30
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

Los reportes nuevos usan opciones solicitadas/aplicadas, idioma fuente/salida, palabras, VAD,
dispositivo, compute type, duración y factor de tiempo real. No incluyen texto. Los reportes
anteriores siguen siendo válidos aunque no contengan esos campos.

## Revisión manual

1. Verificar que el tab distinga siempre modelo seleccionado y modelo residente.
2. Cambiar de `small` a `medium` sin liberar manualmente y confirmar el reemplazo automático.
3. Encolar al menos dos audios, reordenarlos y comprobar ejecución FIFO; cancelar uno en espera sin
   detener el activo.
4. Confirmar que `Texto limpio` sea la vista inicial y que palabras/timestamps sólo aparezcan al
   pedirlos en `Detalle opcional`.
5. Corregir un segmento, buscarlo, restaurarlo y exportar TXT, JSON, SRT, VTT, CSV y TSV. Verificar
   que JSON conserve original, correcciones y resultado renderizado.
6. Probar idioma automático/manual, traducción español→inglés, VAD desactivado/automático/personal,
   hotwords, prompt inicial y uno o varios intervalos.
7. Grabar y transcribir desde el micrófono en el tab Whisper.
8. Dictar desde el botón de micrófono del compositor LLM y comprobar que el texto quede editable.
9. Con un LLM residente en GPU, comprobar que una generación activa no sea interrumpida.
10. Cuando el monitor exija intercambio, comprobar LLM en CPU, Whisper en GPU, liberación de Whisper
   y restauración posterior del LLM en GPU.
11. Confirmar que cerrar la aplicación termina el worker y recupera RAM/VRAM.
12. En `Dictado experimental`, comparar fragmentos 30/3/12 contra la transcripción normal, revisar
    cada frontera y confirmar que cancelar conserva sólo el texto parcial visible.

## OOM deliberado

No existe un comando automático que asigne memoria hasta provocar un OOM. Esta validación requiere
autorización explícita, monitor visible, audio no sensible y un plan de recuperación. Debe comprobar
que el error quede en el worker, que la UI siga operativa, que RAM/VRAM se recuperen y que una tarea
posterior con `small` pueda completar. Hasta ejecutar ese procedimiento, la recuperación OOM sigue
pendiente aunque las rutas de error y reinicio estén cubiertas de forma segura.
