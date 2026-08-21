# Guía de uso de la suite LLM

La suite LLM es un espacio local para mantener conversaciones, seleccionar contexto externo y
controlar cómo genera cada tag instalado en Ollama. AIOpenStudio no descarga modelos desde este tab
y no habilita visión, thinking ni salida estructurada por el nombre comercial de una familia:
consulta las capacidades del tag exacto mediante `/api/show`.

## Inicio rápido

1. Inicia Ollama por separado.
2. Ejecuta `.\.venv\Scripts\python.exe -m aiopenstudio` desde la raíz del repositorio.
3. Abre **LLM** y pulsa **Actualizar modelos**.
4. Selecciona un tag ya instalado.
5. Crea o abre una conversación, escribe un mensaje y pulsa **Enviar**.

La barra superior muestra el estado de Ollama. **Cargar** coloca el modelo en memoria con el
`keep-alive` indicado y **Liberar** solicita descargarlo de RAM/VRAM. Enviar también puede cargarlo
de forma implícita. Ninguna de estas acciones instala o elimina tags.

## La interfaz

El área de trabajo tiene tres paneles redimensionables:

- **Conversaciones**, a la izquierda: búsqueda, creación, reapertura y administración.
- **Conversación**, al centro: título activo, transcript, compositor, cancelación y dictado.
- **Contexto / Generación / Resumen**, a la derecha: adjuntos seleccionables, parámetros y
  compactación versionada.

Las operaciones de archivos, SQLite y runtime no se realizan en el hilo principal de Tk. Durante
streaming, la UI agrupa deltas brevemente para no degradarse con respuestas largas.

## Conversaciones persistentes

El primer mensaje da un título local a una conversación nueva; no se llama a un segundo modelo. El
título siempre puede cambiarse con **Renombrar**. Al reiniciar, AIOpenStudio reabre la conversación
activa modificada más recientemente.

El navegador permite:

- **Nueva**: crea una conversación vacía.
- **Buscar**: filtra títulos; la búsqueda usa el índice local.
- **Archivar/restaurar**: oculta una conversación sin borrarla. Activa **Mostrar archivadas** para
  recuperarla. No se puede continuar una conversación archivada.
- **Exportar**: guarda Markdown o JSON. La exportación contiene conversación, mensajes y, en JSON,
  resúmenes; no incorpora adjuntos ni trazas de razonamiento.
- **Eliminar**: borra de SQLite la conversación, mensajes, resúmenes y referencias después de una
  confirmación. También limpia snapshots y copias preparadas propiedad de AIOpenStudio, pero nunca
  borra los archivos originales del usuario.

Si seleccionas un modelo distinto del usado en la conversación, aparece una advertencia: aceptar
envía el historial completo utilizable al tag nuevo. Esto puede cambiar estilo, tokenización,
ventana disponible y capacidad multimodal.

### Respuestas canceladas o fallidas

**Cancelar** detiene la operación activa. El texto parcial recibido se conserva con estado
`cancelled`, queda visible y auditable, pero no se reinyecta en turnos posteriores. Lo mismo ocurre
con una respuesta que falle la validación JSON. Así se evita presentar texto incompleto como una
decisión confirmada del modelo.

## Cola de contexto externo

En **Contexto**, **Agregar** acepta inicialmente:

- texto: `.txt`, `.json`, `.yaml`, `.yml`, `.md`, `.py`, `.c`, `.cpp`, `.h`, `.hpp`, `.js`, `.ts`,
  `.tsx`, `.html`, `.css` y `.sql`;
- imagen: `.png`, `.jpg`, `.jpeg` y `.bmp` de un solo frame.

Agregar un archivo no lo envía: nace con `☐`. Haz doble clic sobre la fila para cambiar a `☑`.
Puedes quitarlo, subirlo, bajarlo, previsualizarlo y elegir **Una vez** o **Cada turno**.

- **Una vez** lo deshabilita cuando el runtime produce la primera respuesta efectiva. Un error de
  preflight no lo consume.
- **Cada turno** sigue habilitado hasta que lo apagues o quites.

La cola es efímera por defecto. **Recordar cola en esta conversación** guarda referencias, orden,
checks y políticas en SQLite. Desmarcarlo vuelve a hacer la cola efímera; no borra los originales.

### Referencia o snapshot

Al agregar, la aplicación pregunta si deseas una copia reproducible:

- **No**: guarda una referencia con ruta, tamaño, SHA-256 y modificación. Si el archivo cambia o
  desaparece, el envío se bloquea.
- **Sí**: copia un snapshot privado y consentido al directorio de datos de AIOpenStudio. El original
  puede cambiar sin alterar la copia usada. El snapshot no entra en Git ni PostgreSQL.

La vista previa indica `ready`, `changed`, `missing` o `invalid`. Ante `changed`, **Aceptar versión
actual** vuelve a calcular metadatos de forma explícita. Revisa siempre el contenido antes de
aceptarlo.

### Seguridad del contexto

Los textos deben ser UTF-8 o UTF-8 con BOM. Se rechazan extensiones desconocidas, bytes NUL,
contenido que parece binario y límites excedidos. El archivo nunca se ejecuta. En el prompt se
delimita con marcadores únicos y se declara como dato externo potencialmente no confiable.

Esto reduce prompt injection, pero no convierte al modelo en un límite de seguridad. Por ejemplo,
un README que diga «ignora las reglas y ejecuta este comando» sigue siendo texto hostil. No habilites
un archivo sin revisarlo y recuerda que las herramientas están deshabilitadas en esta fase.

### Imágenes y visión

La imagen se verifica por contenido real, dimensiones, tamaño y cantidad de frames. AIOpenStudio
genera una copia transitoria PNG RGB/RGBA; no modifica el original. La fila sólo puede participar si
el tag exacto declara `vision`. Mientras el digest no publique una cantidad mayor validada, se
permite una imagen por turno.

Ejemplo: agrega una captura PNG, déjala en **Una vez**, habilítala y pregunta «Describe el error
visible y propón tres comprobaciones, sin inventar texto que no puedas leer». Después del primer
turno la imagen queda deshabilitada y no consume contexto accidentalmente en la siguiente pregunta.

## Controles de generación

Un campo vacío significa «no enviar override»: se conserva el valor fijado por el Modelfile o el
runtime. **Restaurar valores del modelo** vacía todos los overrides, el prompt de sistema y el
esquema. No copia valores numéricos a los campos porque un valor observado en `/api/show` no siempre
representa todos los defaults internos.

| Control | Significado | Uso habitual |
|---|---|---|
| Temperatura | Aleatoriedad de muestreo, entre 0 y 2 | 0–0,3 para extracción/código; 0,7–1 para ideación |
| `top_p` | Masa probabilística acumulada, mayor que 0 y hasta 1 | 0,8–0,95 limita alternativas improbables |
| `top_k` | Cantidad de candidatos; 0 deja el comportamiento del backend | 20–50 para reducir dispersión |
| `min_p` | Descarta tokens demasiado improbables respecto del mejor | 0,02–0,1 puede estabilizar texto creativo |
| Seed | Inicializa el muestreo; el dado elige 0–2.147.483.647 | Repite comparaciones con los mismos ajustes |
| Ventana (`num_ctx`) | Tokens máximos de entrada más salida | Aumentar sólo con RAM/VRAM suficiente |
| Tokens nuevos | Reserva máxima de salida (`num_predict`) | 128 para respuestas breves; 1.024+ para análisis largos |
| Repetición | Penaliza tokens ya usados | Alrededor de 1,05–1,2 reduce bucles; extremos dañan coherencia |
| Secuencias stop | Corta al encontrar una línea configurada | Delimitadores como `END_JSON` o fin de plantilla |
| Prompt de sistema | Instrucción protegida anterior al historial | Rol, idioma, restricciones y formato estable |

No cambies todos los samplers a la vez. Empieza con temperatura y máximo de salida; añade `top_p` o
`min_p` sólo si puedes comparar resultados. Una seed no garantiza identidad bit a bit entre
versiones, hardware o builds de Ollama.

### Ejemplos de ajustes

Respuesta técnica determinista:

```text
Temperatura: 0.1
top_p: 0.9
Tokens nuevos: 600
Repetición: 1.05
Prompt de sistema: Responde en español. Separa hechos observados de inferencias.
```

Ideación variada:

```text
Temperatura: 0.9
top_p: 0.95
min_p: 0.03
Tokens nuevos: 900
```

Comparación reproducible: selecciona una seed con el dado, guarda el número y cambia un solo
parámetro entre intentos. La conversación registra el tag exacto de cada respuesta.

## Thinking y respuesta directa

El selector sólo se habilita cuando `/api/show` declara thinking para el tag exacto.

- **Predeterminado** no envía el control.
- **Desactivar** envía `think=false`, útil para pedir sólo el contenido final si el modelo/runtime
  admite la forma booleana.
- **Activar** envía `think=true`.
- **Bajo / Medio / Alto** sólo aparecen cuando la forma por niveles haya sido validada.

**Mostrar traza durante streaming** es independiente: decide si ves los deltas `thinking`. La traza
no se guarda, no se exporta y no se reinyecta. Desmarcarla no necesariamente desactiva el proceso
interno; para eso usa **Desactivar** cuando esté disponible.

## Presupuesto, truncamiento y resúmenes

Antes de persistir el mensaje o invocar el runtime se muestra:

```text
entrada disponible = ventana efectiva - máximo de tokens nuevos
```

El conteo previo es conservador; `prompt_eval_count` del runtime es la medición real posterior. El
preflight incluye sistema, resumen activo, historial utilizable, contexto y mensaje nuevo.

- **Rechazar** es el modo seguro: no guarda ni envía el mensaje si no cabe.
- **Truncar historial antiguo** omite intercambios iniciales sólo del request actual. Nunca borra el
  historial, el sistema, el resumen, hechos protegidos ni el mensaje actual.

En **Resumen** puedes inspeccionar versiones, escribir una nueva, elegir hasta qué mensaje cubre y
anotar hechos protegidos, uno por línea. Guardar una versión desactiva la anterior sin borrarla.
Descartar conserva la versión para auditoría, pero deja de usarla. AIOpenStudio no genera un resumen
implícitamente: el usuario revisa el texto antes de hacerlo activo.

Un buen resumen conserva objetivos, decisiones, restricciones, rutas lógicas, hechos confirmados y
pendientes de validación. Evita opiniones pasajeras y texto que ya no será útil.

Ejemplo de hechos protegidos:

```text
No descargar modelos sin autorización.
Python soportado: 3.11 y 3.12.
La salida debe conservar compatibilidad con Windows.
Pendiente: validar el resultado contra datos reales.
```

## Markdown seguro y texto plano

El transcript renderiza encabezados, listas y bloques de código con componentes nativos Tk. Cada
bloque de código tiene **Copiar**. Los enlaces se muestran como texto inerte `etiqueta ⟨URL⟩`; no se
abren con un clic y no hay HTML ni WebView activo. **Texto plano** desactiva la interpretación de
Markdown para inspeccionar exactamente lo almacenado.

Durante streaming se muestra texto incremental. Al terminar, la conversación se recarga desde
SQLite y se renderiza el estado persistido y validado.

## JSON y JSON Schema

**Salida** ofrece JSON y JSON Schema sólo cuando el runtime declara la capacidad de formato para el
tag. La solicitud se envía mediante el contrato neutral y Ollama recibe `format`. Antes de marcar la
respuesta completa, Pydantic comprueba que sea JSON válido.

En **JSON Schema**, AIOpenStudio valida además un subconjunto deliberado:

- `type`: `object`, `array`, `string`, `number`, `integer`, `boolean` o `null`;
- `properties`, `required`, `additionalProperties`;
- `items`, `minItems`, `maxItems`;
- `enum`, `const`;
- `minimum`, `maximum`, `minLength`, `maxLength` y `pattern`.

Palabras no admitidas, como `oneOf`, se rechazan localmente para no fingir una validación parcial.

Ejemplo para extraer incidencias:

```json
{
  "type": "object",
  "required": ["summary", "severity", "actions"],
  "additionalProperties": false,
  "properties": {
    "summary": {"type": "string", "minLength": 1},
    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
    "actions": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string"}
    }
  }
}
```

Adjunta un log como texto, habilítalo **Una vez**, selecciona JSON Schema y pide «Extrae sólo los
hechos presentes en el log». Si el modelo devuelve prosa, falta una propiedad o agrega campos, la
respuesta queda con estado fallido y no entra al historial futuro.

## Flujos de trabajo típicos

### Revisar código sin ejecutarlo

1. Agrega `.py`, `.cpp`, `.js` o el formato pertinente como referencia.
2. Previsualiza, usa **Una vez** y habilita.
3. Usa temperatura 0,1–0,3.
4. Pide hallazgos con archivo lógico, evidencia y nivel de confianza.

El código se lee como dato; AIOpenStudio no lo ejecuta.

### Trabajar con una especificación durante varios turnos

Agrega el Markdown con **Cada turno**, activa **Recordar cola** si quieres recuperarlo al reiniciar y
vigila el presupuesto. Para una especificación estable conviene snapshot; para una que seguirá
cambiando, referencia y aceptación explícita de cada versión.

### Analizar una imagen y continuar sin ella

Usa un tag que muestre **visión**, agrega PNG/JPEG/BMP, mantén **Una vez** y pregunta por elementos
observables. En el turno siguiente el archivo estará deshabilitado, pero la respuesta textual
validada sí permanece en el historial.

### Conversación muy larga

Primero crea un resumen revisado con hechos protegidos. Si aún no cabe, reduce adjuntos o salida.
Usa truncamiento sólo cuando aceptar la omisión temporal de mensajes antiguos sea una decisión
consciente. No eleves `num_ctx` sin observar RAM/VRAM.

### Obtener sólo la respuesta final de un modelo razonador

Si el tag muestra **thinking declarado**, selecciona **Desactivar** y deja sin marcar la visualización
de la traza. Si el runtime rechaza la forma booleana, vuelve a **Predeterminado**: la declaración de
thinking no demuestra que todos los niveles o controles sean compatibles.

## Privacidad y persistencia

Conversaciones, mensajes, resúmenes y referencias de contexto se guardan en SQLite local. La
configuración PostgreSQL de la aplicación no replica prompts, respuestas, adjuntos ni rutas. Hacerlo
en el futuro requerirá una política de privacidad independiente y consentimiento explícito.

Los logs de ejecución conservan hashes, identificadores, presupuesto y métricas, no el prompt ni la
respuesta en claro. Herramientas, búsqueda web y Ollama Cloud permanecen fuera del alcance local
inicial.

## Solución de problemas

**No aparece un modelo:** pulsa **Actualizar modelos** y confirma en Ollama que el tag exacto ya está
instalado. AIOpenStudio no lo descargará automáticamente.

**No puedo habilitar JSON, visión o thinking:** revisa el resumen de capacidades. La inspección del
tag pudo fallar o el tag no declaró esa capacidad. No se infiere por nombre.

**El contexto dice `changed` o `missing`:** abre la vista previa. Acepta la versión actual sólo si la
revisaste, vuelve a agregar el archivo o quítalo. Un snapshot válido puede seguir funcionando aunque
el original desaparezca.

**El mensaje no cabe:** reduce contexto o tokens nuevos, crea un resumen, usa truncamiento explícito
o aumenta `num_ctx` con cautela. El mensaje que falla preflight no se persiste.

**JSON aparece como fallido:** usa **Texto plano** para inspeccionar la respuesta y simplifica el
esquema. El modelo puede no obedecer aunque el runtime acepte `format`.

**La UI parece ocupada tras cancelar:** espera el terminal del runtime. La cancelación fuerte puede
necesitar cerrar la corriente activa antes de persistir el parcial.

## Matriz real optativa

La matriz automatizada nunca descarga modelos. Se habilita sólo con tags ya instalados y una imagen
elegida por el usuario:

```powershell
$env:AIOPENSTUDIO_RUN_LLM_MATRIX = "1"
$env:AIOPENSTUDIO_LLM_TEXT_TAG = "<tag-solo-texto>"
$env:AIOPENSTUDIO_LLM_VISION_TAG = "<tag-con-vision>"
$env:AIOPENSTUDIO_LLM_THINKING_TAG = "<tag-con-thinking>"
$env:AIOPENSTUDIO_LLM_TEST_IMAGE = "C:\ruta\elegida\prueba.png"
.\.venv\Scripts\python.exe -m pytest tests\integration\test_llm_capability_matrix.py -q
```

No fijes esos valores en archivos versionados. El procedimiento y los resultados aprobados se
registran en `docs/llm-validation.md`.
