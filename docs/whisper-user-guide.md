# Guía de uso de Whisper en AIOpenStudio

Esta guía explica la suite de transcripción basada en `faster-whisper 1.2.1`: selección de modelo,
idiomas, traducción, cola de audios, timestamps, VAD, vocabulario, decodificación, correcciones,
exportación y dictado experimental por fragmentos.

AIOpenStudio no descarga modelos desde el tab. Toda inferencia usa snapshots locales y se ejecuta en
un worker separado para que una caída, cancelación fuerte u OOM no derribe la interfaz.

## Inicio rápido

1. Inicia AIOpenStudio con el entorno principal:

   ```powershell
   & .\.venv\Scripts\python.exe -m aiopenstudio
   ```

2. Abre el tab `Whisper` y pulsa `Actualizar`.
3. Selecciona un modelo y `auto`, `cpu` o `gpu` como dispositivo.
4. Pulsa `Seleccionar…` para un audio o `Agregar audios…` para una cola.
5. Mantén `Entrada = automático`, `Tarea = transcribe`, VAD automático y timestamps por palabra
   apagados para la primera prueba.
6. Pulsa `Procesar cola`.
7. Lee el resultado en `Texto limpio`. Abre `Detalle opcional` sólo cuando necesites tiempos,
   confianza o correcciones.
8. Usa `Exportar…` para conservar el resultado.

## Qué hace cada vista

### Texto limpio

Es la vista predeterminada. Muestra solamente el texto continuo, sin anteponer timestamps, índices,
probabilidades ni nombres de campo. Es la opción recomendada para leer, copiar o revisar una
transcripción normal.

La línea superior resume:

- idioma de entrada detectado y su probabilidad;
- idioma de salida;
- CPU/GPU y tipo de cómputo realmente aplicado;
- tiempo descartado por VAD, cuando puede calcularse;
- cantidad de correcciones vigentes.

### Detalle opcional

Muestra una tabla navegable. Los segmentos aparecen siempre; las palabras sólo se incorporan al
activar `Mostrar palabras` y sólo existirán si la transcripción se ejecutó con
`Timestamps por palabra`.

Para cada segmento puede verse:

- inicio y final;
- texto;
- `avg_logprob`, mostrado como `logp`, cuando faster-whisper lo informa;
- marca `✎` cuando el texto fue corregido.

Para cada palabra puede verse su inicio, final y probabilidad. Una probabilidad baja no demuestra
por sí sola que la palabra sea incorrecta: ruido, acento, música, nombres propios y segmentación
pueden reducirla.

La búsqueda no distingue mayúsculas. Si un segmento fue corregido, sus palabras originales se
ocultan porque sus límites temporales ya no describen necesariamente el texto nuevo.

### Ajustes

Contiene VAD, intervalos, contexto lingüístico y decodificación. Un campo avanzado vacío significa
«usar el valor del backend»; no significa cero.

### Dictado experimental

Procesa un archivo ya cerrado en ventanas solapadas y deduplica palabras repetidas entre ventanas.
No escucha continuamente el micrófono, no es streaming nativo y no promete una latencia estable.
Su uso y límites se explican más adelante.

## Modelos

El catálogo inicial incluye:

| Modelo | Tamaño conocido aproximado | Uso recomendado |
|---|---:|---|
| `small` | 463,5 MiB | Primera prueba, dictado, audios claros y CPU |
| `medium` | 1,4 GiB | Mejor precisión multilingüe con más tiempo y memoria |
| `large-v3` | depende del snapshot | Máxima calidad disponible; validar memoria y GPU antes |

El tamaño del archivo no equivale al consumo final de RAM o VRAM. Audio largo, beam search,
timestamps por palabra y contexto aumentan el trabajo temporal.

Las variantes `.en`, si se incorporan en el futuro, aceptan sólo audio inglés. `turbo` puede
transcribir varios idiomas, pero AIOpenStudio no habilita traducción porque ese modelo no fue
entrenado para esa tarea.

Para consultar e instalar explícitamente un snapshot ya catalogado:

```powershell
& .\.venv\Scripts\python.exe scripts\model_library.py list
& .\.venv\Scripts\python.exe scripts\model_library.py download speech.faster-whisper-small
& .\.venv\Scripts\python.exe scripts\model_library.py download speech.faster-whisper-medium
& .\.venv\Scripts\python.exe scripts\model_library.py download speech.faster-whisper-large-v3
```

`download` muestra fuente, licencia y tamaño conocido y exige confirmación. No ejecutes los tres
comandos por costumbre: instala sólo el modelo que necesites.

## Dispositivo y residencia

- `auto`: el runtime elige CUDA cuando CTranslate2 la detecta; en otro caso usa CPU.
- `cpu`: usa `int8` en la configuración actual. Es más lento, pero evita competir por VRAM.
- `gpu`: solicita CUDA y usa `int8_float16` en la configuración actual.

`Cargar / cambiar` hace residente el modelo seleccionado. `Liberar residente` termina o descarga el
worker según la política y recupera memoria. Cambiar de modelo reemplaza el residente anterior.

No confundas:

- modelo seleccionado: el que utilizará la próxima tarea;
- modelo residente: el que ya ocupa RAM o VRAM;
- worker ejecutándose: proceso nativo disponible, aunque el modelo pueda estar descargado.

## Idioma de entrada y salida

`Entrada` describe el idioma hablado en el audio:

- `automático`: Whisper lo detecta y entrega una probabilidad;
- código manual como `es`, `en`, `pt` o `ja`: evita detección y puede mejorar consistencia cuando
  conoces el idioma.

`Tarea` describe la salida:

- `transcribe`: escribe en el idioma del audio;
- `translate`: traduce únicamente a inglés.

No existe un selector de idioma de salida porque Whisper no ofrece destinos arbitrarios. Para el
listado completo de los 100 códigos y las restricciones `.en`/`turbo`, consulta
[Idiomas y tareas de Whisper](whisper-language-support.md).

### Ejemplo: entrevista en español

- Entrada: `es` o `automático`.
- Tarea: `transcribe`.
- Salida: español.

### Ejemplo: subtítulos ingleses desde una entrevista española

- Modelo: `small`, `medium` o `large-v3` multilingüe.
- Entrada: `es`.
- Tarea: `translate`.
- Salida: inglés.
- Exportación: SRT o VTT.

La traducción directa español→francés no está disponible. Debe transcribirse y pasarse después por
un backend de traducción separado.

## Cola FIFO

`Agregar audios…` permite elegir varios archivos. La cola puede reordenarse con `Subir` y `Bajar`
antes de procesarla. `Procesar cola` toma una instantánea del modelo y ajustes actuales y ejecuta un
audio por vez.

Esto no es inferencia batch. La ejecución secuencial ofrece:

- memoria más predecible;
- cancelación por tarea;
- aislamiento de errores;
- orden reproducible.

Si un archivo desapareció o es inválido, esa fila pasa a `falló` y la cola continúa. Una tarea en
espera puede cancelarse sin detener la activa. Los audios agregados mientras una cola ya está en
ejecución quedan pendientes para la próxima pulsación.

## Formatos de audio

El selector ofrece WAV, MP3, M4A, FLAC, OGG, OPUS, WEBM y MP4. PyAV inspecciona y decodifica el
contenedor local. Que una extensión aparezca en el selector no garantiza que cualquier archivo con
esa extensión esté sano: contenedores truncados, cifrados o sin pista de audio fallarán de forma
localizada.

No cambies la extensión para «convertir» un archivo. Usa una herramienta de conversión confiable y
conserva el original fuera del repositorio.

## Timestamps y confianza

Los timestamps de segmento forman parte del resultado normal y alimentan SRT/VTT. Los timestamps
por palabra se solicitan con `Timestamps por palabra` y están apagados de forma predeterminada para
mantener la UI legible y reducir trabajo adicional.

Actívalos cuando necesites:

- alinear una cita exacta;
- localizar una palabra en el audio;
- generar CSV/TSV detallado;
- revisar silencios alucinados.

Evítalos si sólo necesitas texto para leer o resumir.

Métricas disponibles:

| Métrica | Interpretación práctica |
|---|---|
| Probabilidad de palabra | Confianza local del token/palabra, entre 0 y 1 |
| `avg_logprob` | Log-probabilidad media del segmento; menos negativa suele ser mejor |
| `no_speech_prob` | Probabilidad estimada de ausencia de habla |
| `compression_ratio` | Señal para detectar texto anormalmente repetitivo |
| Temperatura | Temperatura usada finalmente tras fallback |

No conviertas estas métricas en una regla automática de verdad. Úsalas para priorizar revisión
humana.

## Corrección no destructiva

1. Abre `Detalle opcional`.
2. Selecciona un segmento, no una palabra.
3. Pulsa `Corregir segmento…` o haz doble clic.
4. Escribe el texto corregido.

La aplicación conserva el resultado original y superpone una corrección. `Restaurar original`
elimina sólo esa corrección.

TXT, SRT, VTT, CSV y TSV se regeneran con el texto corregido. El JSON conserva tres capas:

```json
{
  "schema_version": 1,
  "original": {"segments": []},
  "corrections": [{"segment_index": 3, "text": "Texto corregido"}],
  "rendered": {"segments": []}
}
```

Cuando cambia un segmento completo, AIOpenStudio no inventa alineación por palabra para el texto
nuevo. CSV/TSV emiten ese segmento como una fila corregida sin tiempos de palabra.

## VAD Silero

VAD detecta regiones con voz antes de transcribir.

### Modos

- `disabled`: no descarta silencios. Es obligatorio para intervalos manuales y para las ventanas del
  dictado experimental.
- `automatic`: usa los valores fijados por faster-whisper. Es el valor recomendado inicialmente.
- `custom`: envía sólo los campos completados; los demás conservan valores del backend.

### Parámetros personalizados

| Control | Significado | Efecto de aumentarlo |
|---|---|---|
| Umbral | Probabilidad mínima para iniciar voz | Exige voz más clara; puede perder habla suave |
| Silencio | Umbral negativo para terminar voz | Cambia la histéresis entre voz y silencio |
| Voz mín. ms | Descarta regiones habladas más cortas | Reduce chasquidos; puede perder palabras breves |
| Voz máx. s | Divide regiones continuas demasiado largas | Crea más cortes |
| Silencio mín. ms | Silencio necesario para separar regiones | Une pausas cortas al aumentarlo |
| Padding ms | Audio añadido antes/después de cada región | Protege consonantes en los bordes, con más contexto |

La línea del resultado muestra cuántos segundos fueron descartados cuando el backend entrega
duración original y duración posterior a VAD. No se informa para intervalos porque el recorte y VAD
no serían comparables.

### Receta: audio con pausas largas

1. Aplica el preset `Audio con pausas`.
2. Revisa que el modo sea `custom`.
3. Prueba `Silencio mín. ms = 1200`.
4. Si desaparecen palabras suaves, baja el umbral o vuelve a `automatic`.

### Receta: audio con ruido constante

- Empieza con automático.
- Prueba umbral `0.6` sólo si el ruido se interpreta como habla.
- Conserva padding de 300–500 ms para no cortar inicios.
- Compara siempre contra VAD desactivado en un fragmento corto.

## Intervalos

El campo acepta rangos separados por coma:

```text
0-30, 01:15-02:00.5, 01:10:00-01:12:30
```

Se admiten segundos, `MM:SS` y `HH:MM:SS`. Los intervalos deben estar ordenados, no solaparse y
tener final posterior al inicio. Al usarlos, selecciona VAD `disabled`.

Casos típicos:

- excluir una introducción musical: por ejemplo `30-600` si el audio termina en el segundo 600;
- transcribir sólo preguntas concretas: `01:10-02:00, 05:30-06:20`;
- comparar parámetros rápidamente sobre treinta segundos: `120-150`.

No se admite la palabra `fin`; escribe el segundo real. La vista experimental crea sus propios
intervalos y no puede combinarse con este campo.

## Prompt inicial, prefijo y hotwords

### Prompt inicial

Da al modelo texto previo de contexto. Sirve para estilo, puntuación y vocabulario:

```text
Reunión técnica de AIOpenStudio sobre CTranslate2, PostgreSQL y PyraCanny.
```

No es una instrucción ejecutable ni una garantía de que todos los términos aparezcan.

### Prefijo

Fuerza o condiciona el comienzo del primer segmento. Úsalo sólo cuando conozcas el inicio esperado.
Un prefijo incorrecto puede sesgar la salida.

### Hotwords

Prioriza nombres propios o vocabulario técnico:

```text
AIOpenStudio, faster-whisper, CTranslate2, PostgreSQL, Ollama, Fooocus
```

En faster-whisper, prefijo y hotwords son incompatibles. AIOpenStudio rechaza la combinación antes
de iniciar el worker. La inferencia batch permanece deshabilitada; no se aplican las diferencias de
hotwords del pipeline batch.

## Presets

- `Valores del backend`: limpia overrides avanzados y vuelve a VAD automático.
- `Rápido`: beam 1, best-of 1 y temperatura 0. Reduce búsqueda; puede bajar precisión.
- `Preciso`: beam 5, best-of 5 y fallback 0→1. Prioriza calidad sobre tiempo.
- `Audio con pausas`: VAD personalizado con umbral 0.5 y silencio mínimo de 1200 ms.

Los presets son puntos de partida, no resultados certificados. Aplicarlos no inicia una tarea.

## Decodificación avanzada

| Parámetro | Significado | Orientación |
|---|---|---|
| Beam | Cantidad de hipótesis de beam search | 1 es rápido; 5 es el valor habitual |
| Best of | Candidatos con muestreo | Tiene mayor efecto fuera de beam search |
| Patience | Cuánto prolongar beam search | Más alto explora más y tarda más |
| Temperaturas | Secuencia de fallback | `0, 0.2, …, 1` aumenta diversidad tras fallos |
| Compresión | Umbral de texto demasiado comprimido/repetitivo | Bajo puede activar fallback con más frecuencia |
| Logprob | Umbral de log-probabilidad | Controla cuándo una hipótesis se considera pobre |
| No habla | Umbral de silencio/no-habla | Interactúa con `no_speech_prob` y logprob |
| Repetición | Penalización de tokens repetidos | Valores alejados del backend pueden dañar nombres |
| N-gram | Prohíbe repetir n-gramas del tamaño indicado | 0 conserva el backend sin prohibición |
| Tokens nuevos | Límite por ventana de decodificación | Muy bajo puede truncar segmentos |
| Silencio alucinado | Detecta silencios anómalos | Requiere timestamps por palabra |
| Detección idioma | Probabilidad mínima de detección | Útil sólo con idioma automático |
| Segmentos detección | Ventanas usadas para detectar idioma | Más segmentos aumentan trabajo |
| Puntuación previa | Signos que se adjuntan a la palabra siguiente | Control avanzado de alineación |
| Puntuación posterior | Signos que se adjuntan a la palabra anterior | Control avanzado de alineación |
| Texto previo | Condiciona cada ventana con salida anterior | Mejora continuidad; puede propagar errores |

El contrato del backend conserva además `length_penalty`, `suppress_blank`, `suppress_tokens` y
`prompt_reset_on_temperature` para adaptadores o configuraciones futuras. La UI no los expone de
forma libre porque requieren conocimiento de tokens o tienen valores predeterminados más seguros.

### Cuándo usar temperatura de fallback

La secuencia `0, 0.2, 0.4, 0.6, 0.8, 1` comienza determinista. Si una hipótesis incumple umbrales de
compresión o logprob, faster-whisper reintenta con una temperatura mayor. Usar sólo `0` mejora
reproducibilidad, pero elimina esa salida de emergencia.

### Condicionamiento con texto previo

- `backend`: conserva el valor fijado.
- `sí`: favorece continuidad entre segmentos.
- `no`: aísla segmentos y puede evitar que un error se propague en audio largo.

Para canciones, repeticiones o alucinaciones persistentes, compara `no`. Para una conferencia
continua, `sí` suele mantener mejor terminología.

## Exportaciones

| Formato | Contenido |
|---|---|
| TXT | Texto corregido continuo |
| SRT | Segmentos corregidos y tiempos compatibles con reproductores |
| VTT | Segmentos corregidos en WebVTT |
| CSV | Filas de segmentos/palabras separadas por coma |
| TSV | Mismas columnas separadas por tabulación |
| JSON | Resultado original, correcciones y resultado renderizado completo |

CSV/TSV incluyen índices, tiempos, texto, probabilidad de palabra, logprob, no-habla, compresión,
temperatura y marca de corrección. JSON añade modelo, dispositivo, idioma, opciones solicitadas y
aplicadas, segmentos, palabras y métricas.

Las exportaciones se escriben primero como `.partial` y se reemplazan al terminar para evitar dejar
un archivo final incompleto.

## Dictado por micrófono

`Grabar micrófono` captura mono PCM de 16 kHz mediante `sounddevice`. Al detener, crea un WAV
temporal, lo agrega a la cola y lo transcribe. La grabación temporal se elimina después del flujo;
exporta el resultado si deseas conservarlo.

El botón de micrófono del tab LLM usa el mismo backend. Si hace falta ceder VRAM, espera al LLM,
mueve el modelo administrado a RAM, transcribe, libera Whisper y restaura el LLM.

Esto tampoco es streaming: la transcripción comienza después de detener la grabación.

## Dictado experimental por fragmentos

Selecciona un audio existente y abre `Dictado experimental`.

Parámetros:

- `Fragmento`: duración de cada ventana, entre 5 y 300 segundos;
- `Solapamiento`: segundos repetidos con la ventana siguiente; debe ser menor que el fragmento;
- `Máx. palabras deduplicadas`: límite de palabras comparadas entre el final acumulado y el inicio
  nuevo.

Ejemplo con fragmento 30 y solapamiento 5 para un audio de 70 segundos:

```text
ventana 1:  0–30
ventana 2: 25–55
ventana 3: 50–70
```

La deduplicación:

1. separa ambos bordes por espacios;
2. normaliza mayúsculas y puntuación;
3. busca la coincidencia más larga dentro del límite;
4. omite ese prefijo de la ventana nueva.

Es una heurística. Puede fallar con reformulaciones, puntuación distinta, tartamudeos, palabras
compuestas o repeticiones reales. La vista informa cuántas palabras eliminó en cada unión.

VAD se desactiva dentro de las ventanas porque faster-whisper no aplica conjuntamente VAD y clips
temporales de la forma necesaria para este flujo. El resultado experimental es transitorio: usa
`Copiar texto` si quieres conservarlo. No se mezcla automáticamente con el documento corregible ni
con la conversación LLM.

Configuración sugerida:

- dictado claro: fragmento 30 s, solapamiento 3 s, deduplicación 12 palabras;
- habla lenta con pausas: 45 s, 5 s, 16 palabras;
- revisión de latencia: 10–15 s, 2–3 s, 10 palabras, sabiendo que abrir más ventanas añade overhead.

## Recetas completas

### Reunión técnica

- Modelo: `medium` si la memoria lo permite.
- Entrada: idioma conocido.
- VAD: automático.
- Prompt inicial: tema de la reunión.
- Hotwords: nombres de personas, productos y siglas.
- Texto previo: backend o sí.
- Exporta JSON y TXT.

### Entrevista para subtítulos

- Tarea: `transcribe` o `translate` a inglés.
- Timestamps por palabra: apagados inicialmente.
- Corrige nombres en segmentos.
- Exporta SRT/VTT.
- Activa palabras sólo para reparar sincronización puntual.

### Clase o conferencia larga

- Encola por archivos o capítulos.
- Modelo: medium/large-v3 después de validar memoria.
- Prompt inicial con asignatura y terminología.
- Texto previo: sí, salvo propagación de errores.
- JSON reproducible para conservar parámetros.

### Podcast con introducción musical

- Usa un intervalo que comience después de la música o VAD automático.
- Si la música produce texto alucinado, compara texto previo `no` y timestamps por palabra con
  silencio alucinado.
- No eleves umbrales y temperaturas simultáneamente; cambia una familia de controles por vez.

### Código y nombres técnicos

- Hotwords: `Python, Tkinter, SQLAlchemy, PostgreSQL, Pydantic`.
- Prompt inicial con una frase natural sobre el tema.
- Temperatura inicial 0.
- Revisa manualmente símbolos y nombres: Whisper transcribe habla, no compila código.

### Audio con varios idiomas

- Entrada automático.
- Observa probabilidad detectada.
- Para cambios frecuentes de idioma, procesa intervalos separados y fija el idioma de cada uno si
  lo conoces.
- No asumas que una única detección representa todo el archivo.

### Audio sensible

- Usa sólo archivos locales.
- Mantén PostgreSQL en una política que no replique contenido; AIOpenStudio registra metadatos y
  hashes, no el texto por defecto.
- Exporta a una ubicación controlada.
- Elimina manualmente el archivo original sólo después de verificar la copia necesaria.

## Persistencia y privacidad

La aplicación conserva metadatos de ejecución según el modo SQLite/PostgreSQL: estado, modelo,
hashes, tiempos y opciones. No replica por defecto audio ni texto transcrito. La cola, documentos
editables y vista experimental viven en memoria durante la sesión; las exportaciones pertenecen al
usuario.

Una corrección no elimina ni modifica el audio. `Quitar` una fila de la cola tampoco borra el archivo
fuente.

## Cancelación, fallos y recuperación

- Cancelar una tarea en espera la retira antes de cargarla.
- Cancelar la activa solicita parada cooperativa entre segmentos.
- Si no responde durante el plazo, el worker puede terminarse para recuperar memoria.
- Un crash u OOM queda contenido en el proceso Whisper.
- La siguiente operación puede recrear el worker dentro del presupuesto de reinicios.

Una cancelación puede conservar un resultado parcial si el backend alcanzó a producir segmentos.
Revísalo antes de exportar.

## Límites deliberados

- Traducción sólo a inglés.
- Sin diarización ni identificación de hablantes nativa.
- Sin streaming nativo.
- `BatchedInferencePipeline` deshabilitado hasta medir memoria, cancelación y diferencias de salida.
- Sin descarga de modelos desde la UI.
- Sin edición de audio ni eliminación automática de archivos del usuario.
- Sin alineación inventada para palabras corregidas.

Incorporar diarización exigiría otro backend, modelos, licencias y presupuesto de memoria; no es una
opción oculta de Whisper.

## Validación segura

Estas comprobaciones no cargan modelos:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy src
& .\.venv\Scripts\python.exe scripts\validate_whisper_vertical.py preflight
```

## Matriz real optativa

Los comandos siguientes cargan un modelo local y requieren un audio no sensible. No descargan
pesos, pero consumen CPU/GPU y generan un JSON sin texto en `data/outputs/whisper-validation/`.

```powershell
# CPU y GPU
python scripts\validate_whisper_vertical.py cpu --model small --source C:\ruta\audio.wav
python scripts\validate_whisper_vertical.py gpu --model small --source C:\ruta\audio.wav

# Cancelación fuerte
python scripts\validate_whisper_vertical.py cancel --model small --source C:\ruta\audio-largo.wav

# Traducción a inglés
python scripts\validate_whisper_vertical.py translate --model small --language es --source C:\ruta\audio-es.wav

# Palabras, VAD y vocabulario
python scripts\validate_whisper_vertical.py word-timestamps --model small --source C:\ruta\audio.wav
python scripts\validate_whisper_vertical.py vad --model small --source C:\ruta\audio.wav
python scripts\validate_whisper_vertical.py hotwords --model small --hotwords "AIOpenStudio,CTranslate2" --source C:\ruta\audio.wav

# Intervalos
python scripts\validate_whisper_vertical.py intervals --model small --source C:\ruta\audio.wav --interval 0-10 --interval 20-30
```

Repite CPU/GPU con `--model medium` sólo después de comprobar memoria. Micrófono, intercambio real
con LLM y OOM deliberado permanecen manuales. Un OOM nunca debe provocarse sin autorización
explícita, monitor visible y un plan para recuperar el worker.

La matriz, resultados aprobados y pendientes están en [Validación de Whisper](whisper-validation.md).

## Solución rápida de problemas

### No aparecen modelos

- Ejecuta `scripts/model_library.py list`.
- Comprueba la raíz configurada.
- No escribas un identificador remoto en la UI.

### Traducción no disponible

- Comprueba que el modelo sea multilingüe.
- `.en` y `turbo` no ofrecen esta tarea en AIOpenStudio.
- El único destino es inglés.

### No aparecen palabras

- Debías activar timestamps por palabra antes de ejecutar.
- Reprocesa el audio; no pueden reconstruirse desde segmentos existentes.

### Intervalos rechazados

- Desactiva VAD.
- Ordena los rangos y elimina solapamientos.
- Usa segundos, `MM:SS` o `HH:MM:SS`.

### Hotwords rechazadas

- Borra el prefijo: ambos son incompatibles.

### La vista experimental repite o elimina texto real

- Reduce el solapamiento o el máximo de palabras deduplicadas.
- Aumenta el fragmento para crear menos fronteras.
- Usa la transcripción normal para el resultado final.

### OOM o worker caído

- Libera el modelo residente.
- Cambia a `small` o CPU.
- Revisa Diagnósticos y VRAM libre.
- No repitas automáticamente una configuración que ya agotó memoria.

Para más síntomas consulta [Solución de problemas](troubleshooting.md).
