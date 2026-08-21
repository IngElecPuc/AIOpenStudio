# Diagnósticos y recuperación

## Logs estructurados

AIOpenStudio escribe eventos JSON Lines en `data/logs/aiopenstudio.jsonl`, con rotación de 5 MiB
y tres copias. Cada evento incluye timestamp UTC, nivel, logger, nombre del evento y `session_id`.
Cuando corresponde también incorpora suite, runtime, modelo, `operation_id`, estado y paso de cierre.

El filtro elimina asignaciones de contraseñas, tokens, secretos, URLs con credenciales y nombres de
usuario presentes en rutas home comunes. No deben registrarse prompts, respuestas, transcripciones,
imágenes, audios ni secretos como campos estructurados.

## Paquete de diagnóstico

`Configuración → Diagnósticos…` muestra comprobaciones de sistema, rutas, runtimes y
persistencia. `Exportar ZIP redactado…` crea:

- `diagnostics.json`: versión, sesión, entorno y comprobaciones;
- hasta 256 KiB finales de cada log JSONL rotado, redactados nuevamente al exportar.

No incluye bases, perfiles PostgreSQL, variables de entorno, modelos, prompts, respuestas,
transcripciones, audios ni imágenes. El usuario debe revisar el ZIP antes de compartirlo.

## Recuperación y cierre

- Las ejecuciones `queued` o `running` de una sesión anterior se marcan `interrupted` al restaurar
  persistencia. La hora de inicio de la sesión impide tocar operaciones actuales.
- Whisper y Fooocus pueden recrear un proceso caído en la siguiente operación, con un máximo
  predeterminado de tres reinicios en cinco minutos. Superado el presupuesto, la suite queda
  degradada y pide revisar Diagnósticos.
- El cierre deja de admitir una segunda solicitud, cierra Fooocus, micrófono, monitor, Whisper y el
  cliente Ollama con plazos acotados, y cierra persistencia al final para conservar estados
  terminales. AIOpenStudio nunca termina el servidor Ollama externo durante este cierre.

Estos mecanismos no sustituyen las validaciones reales de concurrencia, presión de memoria y OOM
pendientes de la fase de robustez.
