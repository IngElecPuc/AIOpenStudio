# Guía de uso de Fooocus en AIOpenStudio

Esta guía explica cómo usar la suite Fooocus de AIOpenStudio para crear, transformar, describir y
mejorar imágenes. Está escrita para la integración con **Fooocus v2.5.5** y describe los controles
que realmente expone el tab de escritorio.

> **Estado actual:** texto a imagen, el proceso supervisado y las cancelaciones básicas ya fueron
> validados con hardware real. Las funciones avanzadas están implementadas, pero sus 23 activos
> auxiliares todavía no se han adquirido. Si una operación necesita uno de ellos, AIOpenStudio la
> detendrá antes de usar la GPU y mostrará qué archivo falta. Este comportamiento es intencional:
> nunca debe producirse una descarga implícita.

## Contenido

- [Inicio rápido](#inicio-rápido)
- [Cómo está organizado el tab](#cómo-está-organizado-el-tab)
- [Parámetros comunes](#parámetros-comunes)
- [Cómo escribir prompts](#cómo-escribir-prompts)
- [Operaciones disponibles](#operaciones-disponibles)
- [Imágenes de referencia](#imágenes-de-referencia)
- [Enhance y mejora por regiones](#enhance-y-mejora-por-regiones)
- [Galería, cola y archivos](#galería-cola-y-archivos)
- [Recetas prácticas](#recetas-prácticas)
- [Diagnóstico y problemas frecuentes](#diagnóstico-y-problemas-frecuentes)
- [Límites y uso responsable](#límites-y-uso-responsable)

## Inicio rápido

Desde PowerShell, en la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe -m aiopenstudio
```

Después:

1. Abre el tab **Fooocus**.
2. Pulsa **Actualizar** si el estado todavía dice que la comprobación está pendiente.
3. Selecciona un **Checkpoint**.
4. Selecciona la operación **Texto a imagen**.
5. En **Prompts**, escribe una descripción.
6. Mantén inicialmente `speed`, `1024×1024`, una imagen, `Guidance 4`, `Sharpness 2` y estilo
   `Fooocus V2`.
7. Pulsa **Añadir a cola**.
8. Sigue el estado en **Cola y ejecuciones** y abre la miniatura resultante con un clic.

Ejemplo mínimo:

```text
Prompt:
a cozy reading room, large window overlooking a rainy forest, warm indirect lighting,
wood and linen, realistic interior photography, high detail

Prompt negativo:
people, text, watermark, logo, distorted furniture, oversaturated colors
```

El inglés suele ofrecer resultados más predecibles con checkpoints SDXL, aunque se pueden usar
prompts en español. Si el resultado no es bueno, cambia una variable a la vez y reutiliza la misma
semilla.

## Cómo está organizado el tab

La columna izquierda contiene la configuración. La derecha muestra la cola, la galería y las
descripciones.

### Controles superiores

- **Checkpoint:** modelo base utilizado por Fooocus.
- **Operación:** tarea principal, por ejemplo texto a imagen, inpaint o Image Prompt.
- **Rendimiento, Tamaño y Formato:** coste, resolución y codificación del resultado.
- **Imágenes, Guidance, Sharpness y Seed:** cantidad y comportamiento de la generación.
- **Estilos:** nombres de estilos Fooocus separados por comas.

### Pestañas de configuración

- **Prompts:** prompt principal y prompt negativo.
- **Fuente y máscara:** imagen que se transformará, máscara de inpaint, direcciones de outpaint y
  opciones de Describe.
- **Referencias:** cola de imágenes para Image Prompt, PyraCanny, CPDS y FaceSwap.
- **Enhance:** una o varias etapas de detección y mejora localizada.

### Resultados

- **Cola y ejecuciones:** trabajos en espera, activos, completados, cancelados o fallidos.
- **Galería:** miniaturas de la sesión y navegación ampliada.
- **Descripción:** texto devuelto por la operación Describe.

## Parámetros comunes

### Checkpoint

El checkpoint determina el conocimiento visual, la estética y el tipo de imágenes que el modelo
produce con más facilidad. Los checkpoints disponibles dependen de la biblioteca configurada.

En el entorno actualmente inventariado:

| Checkpoint | Punto fuerte orientativo | Casos típicos |
|---|---|---|
| `juggernautXL_v8Rundiffusion.safetensors` | Uso general, fotografía y estética cinematográfica | Retratos, paisajes, fantasía realista, escenas narrativas |
| `realisticStockPhoto_v20.safetensors` | Fotografía realista/editorial | Producto, arquitectura, personas, imágenes de catálogo |
| `animaPencilXL_v500.safetensors` | Anime, manga e ilustración | Personajes, concept art, pósteres ilustrados |

Estas descripciones son orientativas. Un checkpoint no garantiza un estilo ni una identidad exacta.
Para comparar modelos, conserva prompt, semilla, dimensiones y demás parámetros.

### Rendimiento

| Valor en la UI | Uso recomendado | Compromiso |
|---|---|---|
| `speed` | Bocetos, exploración y búsqueda de composición | Menos tiempo y normalmente menos refinamiento |
| `quality` | Resultado final o comparación de detalles | Más pasos y mayor tiempo de GPU |
| `extreme_speed` | Iteración muy rápida cuando el checkpoint y los activos lo soporten | Cambia el método de generación y puede perder fidelidad |

Flujo aconsejado: explora en `speed`, fija la idea y la semilla, y prueba `quality` al final. No
asumas que `quality` será una copia más nítida: puede producir una interpretación diferente.

### Tamaño y relación de aspecto

La UI ofrece:

| Tamaño | Relación aproximada | Buen uso |
|---|---:|---|
| `1024×1024` | 1:1 | Avatares, producto, arte cuadrado, exploración general |
| `1152×896` | Horizontal moderado | Fotografía, grupos, interiores, paisajes |
| `896×1152` | Vertical moderado | Retratos, moda, personajes |
| `1344×768` | Panorámico | Banner, cine, paisaje, cabecera web |
| `768×1344` | Vertical alto | Póster, cuerpo entero, portada |

La relación de aspecto influye en la composición. Para un cuerpo entero suele funcionar mejor una
orientación vertical; para dos sujetos o un ambiente, una horizontal. Generar cuadrado y recortar
después puede eliminar información importante.

### Cantidad de imágenes

**Imágenes** admite de 1 a 8 resultados por trabajo.

- Usa 1 para pruebas controladas o cuando la VRAM sea limitada.
- Usa 2–4 para explorar variaciones de una idea.
- Usa 8 sólo si el tiempo, disco y memoria disponibles lo permiten.

Cada imagen es un output separado dentro de la misma ejecución. Más imágenes no significa que se
creen simultáneamente ni que compartan exactamente la misma composición.

### Formato de salida

| Formato | Ventaja | Cuándo usarlo |
|---|---|---|
| `png` | Sin pérdida, adecuado para edición posterior | Máscaras, ilustración, archivo maestro |
| `jpeg` | Menor tamaño | Fotografías destinadas a compartir o publicar |
| `webp` | Buena compresión y calidad | Web y almacenamiento compacto cuando el software destino lo admita |

Los formatos de **entrada** son distintos: AIOpenStudio sólo acepta actualmente `.png`, `.jpg`,
`.jpeg` y `.bmp`. Que WEBP sea un formato de salida no significa que todavía pueda cargarse como
referencia.

### Guidance

**Guidance Scale** controla cuánto se orienta la generación por el prompt. Rango admitido: 1–30;
valor inicial: 4.

- `2–3`: interpretación más libre, suave o fotográfica.
- `4–6`: intervalo general recomendado.
- `7–10`: mayor insistencia en conceptos, con riesgo de rigidez o saturación.
- Valores muy altos: pueden empeorar color, textura, anatomía y naturalidad.

Si faltan elementos importantes, prueba primero aclarar el prompt. Aumentar Guidance no corrige un
prompt ambiguo ni garantiza texto legible.

### Sharpness

**Sharpness** controla el énfasis visual en bordes y microdetalle. Rango: 0–30; valor inicial: 2.

- `0–1`: imagen más suave; útil para piel, niebla, ilustración pictórica.
- `2–3`: punto de partida equilibrado.
- `4–6`: producto, arquitectura o texturas que necesitan definición.
- Valores altos: pueden producir halos, piel artificial, ruido y contornos excesivos.

Sharpness no reemplaza Upscale. Primero corrige composición y contenido; después mejora tamaño y
detalle.

### Seed o semilla

La semilla inicializa el ruido de la generación.

- Déjala vacía para obtener una semilla aleatoria.
- Escribe un entero no negativo para repetir una configuración.
- Pulsa el botón **⚅** situado junto al campo para generar inmediatamente una semilla válida entre
  `0` y `2^63−1`; el botón sólo rellena el campo y no añade una tarea a la cola.
- Conserva semilla, checkpoint, prompt, estilos, tamaño y rendimiento al comparar un solo cambio.

La reproducibilidad no es una promesa de identidad bit a bit entre distintas versiones de Fooocus,
controladores, hardware o checkpoints.

### Estilos

El campo **Estilos (separados por coma)** se envía como una lista. Ejemplo:

```text
Fooocus V2, Fooocus Photograph, Fooocus Cinematic
```

Usa nombres que existan en la instalación descubierta. El valor inicial `Fooocus V2` es una base
segura. Combinar muchos estilos puede generar instrucciones contradictorias; empieza con uno y
agrega otro sólo si cumple una función clara.

### Prompt negativo

Indica elementos o defectos que deseas evitar:

```text
text, watermark, logo, low resolution, deformed hands, duplicated objects, oversaturated colors
```

No lo conviertas en una lista enorme por defecto. Un negativo excesivo puede restringir demasiado
la imagen o eliminar rasgos necesarios. Usa términos concretos relacionados con los fallos que
observaste.

## Cómo escribir prompts

Una estructura útil es:

```text
sujeto + acción + entorno + composición + iluminación + medio/estilo + cámara + cualidades
```

Ejemplo fotográfico:

```text
an elderly Chilean botanist examining native flowers in a greenhouse, medium portrait,
eye-level camera, soft morning window light, realistic editorial photography, natural skin,
subtle colors, 50mm lens, shallow depth of field
```

Ejemplo ilustrado:

```text
a young astronomer on a rooftop observatory, wind moving her coat, large moon and intricate
telescopes, dynamic low-angle composition, ink and watercolor illustration, deep indigo and gold,
fine linework
```

### Buenas prácticas

1. Describe primero lo indispensable.
2. Usa relaciones espaciales explícitas: “a la izquierda”, “detrás”, “plano entero”.
3. Evita adjetivos redundantes que no cambian la escena.
4. Para fotografía, añade iluminación, lente o distancia de cámara sólo cuando sean relevantes.
5. Para producto, especifica fondo, material, ángulo y uso comercial.
6. Para personajes, indica plano, pose, vestuario y edad adulta cuando corresponda.
7. Itera con semilla fija y cambia una sección a la vez.

### Limitaciones conocidas de los prompts

- El texto dentro de las imágenes puede ser incorrecto o inventado.
- Manos, objetos repetidos y multitudes siguen siendo difíciles.
- “Exactamente igual” no es una instrucción fiable para rostros, logotipos ni productos.
- El modelo puede heredar sesgos de sus datos y del checkpoint.
- Una referencia visual suele controlar estructura o identidad mejor que añadir muchos adjetivos.

## Operaciones disponibles

La lista se construye desde las capacidades detectadas en el esquema Gradio. Si una operación no
aparece, pulsa **Actualizar**. Si sigue ausente, la instalación fijada no la está exponiendo.

### Disponibilidad y activos requeridos

Que una operación aparezca significa que Fooocus entiende sus parámetros; no garantiza que sus
pesos auxiliares estén instalados. Al pulsar **Añadir a cola**, AIOpenStudio hace un preflight
específico antes de reservar GPU.

| Capacidad | Activos adicionales principales |
|---|---|
| Texto a imagen | Ninguno fuera de los activos base y el checkpoint |
| Variación sutil/fuerte | Ninguno fuera de los activos base y el checkpoint |
| Upscale | Modelo upscaler de Fooocus |
| Inpaint/Outpaint | Cabezal y patch de inpaint v2.6 |
| Image Prompt | CLIP Vision, negativo IP-Adapter e IP-Adapter SDXL |
| PyraCanny | Control-LoRA Canny |
| CPDS | ControlNet CPDS |
| FaceSwap | CLIP Vision, negativo IP-Adapter y adaptador facial |
| Describe Fotografía | Interrogador BLIP |
| Describe Arte/Anime | Modelo y etiquetas WD 1.4 |
| Enhance con SAM | Inpaint, GroundingDINO cuando hay detección y checkpoint SAM elegido |
| Enhance con REMBG | Inpaint y ONNX del modelo de máscara elegido |

El mensaje de preflight muestra la ruta exacta que falta. La guía enseña el uso funcional, pero no
autoriza la adquisición de esos archivos.

Requisitos de entrada:

| Operación | Prompt | Fuente | Máscara | Referencias |
|---|---|---|---|---|
| Texto a imagen | Obligatorio | No | No | No; deshabilítalas o selecciona Image Prompt |
| Variación/Upscale | Opcional | Obligatoria | No | Opcionales con **Mezclar** activado |
| Inpaint | Opcional; se recomienda prompt adicional | Obligatoria | Obligatoria | Opcionales con **Mezclar** activado |
| Outpaint | Opcional | Obligatoria | No; exige una dirección | Opcionales con **Mezclar** activado |
| Image Prompt | Opcional | No | No | Al menos una habilitada |
| Describe | Vacío | Obligatoria | No | No |
| Enhance | Opcional | Obligatoria | Automática por etapa | No en la operación actual |

### Texto a imagen

Genera desde un prompt sin imagen fuente. Es la operación más adecuada para ideación.

Requisitos:

- Prompt principal no vacío.
- Checkpoint disponible.

Flujo:

1. Selecciona **Texto a imagen**.
2. Escribe prompt y, si hace falta, prompt negativo.
3. Elige relación de aspecto y checkpoint.
4. Genera una o varias alternativas.

Casos típicos:

- Fotografía ficticia y editorial.
- Arte conceptual y personajes.
- Fondos, paisajes e interiores.
- Mockups visuales y producto conceptual.
- Portadas y composiciones para campañas, agregando el texto final en un editor convencional.

### Variación sutil

Crea una reinterpretación cercana de una imagen fuente. Busca conservar más composición, tema y
apariencia general que una variación fuerte.

Requisitos:

- **Imagen fuente** en la pestaña **Fuente y máscara**.
- PNG, JPEG o BMP válido.

Úsala para:

- Probar una expresión, iluminación o textura ligeramente diferente.
- Refinar una composición elegida.
- Crear alternativas de un producto conceptual.
- Variar una ilustración sin abandonar su estructura general.

El prompt puede estar vacío, pero uno breve ayuda a declarar qué debe conservarse o cambiarse:

```text
same composition, softer morning light, natural colors, realistic materials
```

### Variación fuerte

Reinterpreta con mayor libertad la fuente. Es útil cuando la idea es buena pero la imagen todavía
no lo es.

Úsala para:

- Buscar otra pose o distribución.
- Convertir un boceto en una escena más desarrollada.
- Explorar estilos manteniendo el tema.
- Salir de defectos repetidos de una generación anterior.

Si necesitas conservar contornos exactos, usa PyraCanny; si necesitas conservar estructura o
profundidad general, prueba CPDS.

### Upscale 1,5×

Aumenta las dimensiones aproximadamente 1,5 veces e intenta mejorar detalle. Es un buen primer paso
cuando 2× modifica demasiado la imagen.

Usos:

- Preparar una imagen para una presentación.
- Mejorar una selección antes de editarla.
- Aumentar moderadamente una fotografía generada.

### Upscale 2×

Aumenta aproximadamente al doble. Consume más tiempo y puede reinterpretar detalle fino.

Usos:

- Resultado final de mayor tamaño.
- Ilustración o producto que necesita más textura.
- Preparación para un recorte posterior.

Revisa rostros, dedos, patrones, texto y bordes después del proceso. Upscale generativo no equivale
a una ampliación matemática.

### Upscale rápido 2×

Prioriza velocidad. Úsalo para evaluar tamaño o composición antes de gastar tiempo en un upscale de
mayor calidad.

No es la primera opción para restauración delicada, tipografía, diagramas ni activos que deban
conservar píxeles exactos.

### Inpaint

Regenera únicamente la región indicada por una máscara.

Requisitos:

- **Imagen fuente**.
- **Máscara** independiente.
- La zona blanca de la máscara representa normalmente la región que se procesará. Verifica siempre
  el resultado y usa **Invertir máscara** dentro de Enhance cuando corresponda.

Flujo:

1. Selecciona **Inpaint**.
2. Carga la imagen fuente.
3. Carga una máscara PNG/JPEG/BMP con el área objetivo claramente separada.
4. Elige el modo de Inpaint.
5. Escribe el **Prompt adicional** describiendo sólo el contenido de la zona.
6. Usa el prompt general para mantener coherencia global si es necesario.

Modos:

| Modo | Intención | Ejemplo |
|---|---|---|
| `default` | Relleno o reparación equilibrada, conservando contexto | Quitar un objeto, completar un fondo |
| `detail` | Mejorar una zona existente con menor transformación | Ojos, manos, joyería, textura |
| `modify` | Reemplazar contenido o cambiarlo con más libertad | Cambiar ropa, añadir objeto, modificar fondo |

Ejemplo para eliminar un automóvil del fondo:

```text
Prompt general:
a quiet cobblestone street at sunrise, historic architecture, realistic photography

Prompt adicional:
empty cobblestone street continuing naturally, matching buildings and morning shadows

Prompt negativo:
car, vehicle, traffic, distorted pavement
```

La máscara debe cubrir el automóvil y un margen pequeño. Una máscara demasiado ajustada puede
dejar bordes; una demasiado amplia puede alterar zonas correctas.

### Outpaint

Expande la imagen hacia una o más direcciones y genera contenido nuevo alrededor.

Requisitos:

- Imagen fuente.
- Al menos una dirección: `left`, `right`, `top` o `bottom`.

Usos típicos:

- Convertir un cuadrado en banner horizontal.
- Dar espacio negativo para texto.
- Extender un fondo o paisaje.
- Reencuadrar un retrato o completar un cuerpo.

Ejemplo para transformar un producto cuadrado en cabecera:

```text
Direcciones: right

Prompt:
minimalist studio photograph of a ceramic coffee maker on the left, warm beige seamless
background extending to the right, soft shadows, premium product advertising, empty copy space

Prompt negativo:
text, logo, extra products, hard seam, duplicated object
```

Extiende una dirección por vez cuando la continuidad sea crítica. Expandir cuatro lados a la vez
da más libertad y puede alterar la percepción del sujeto.

### Image Prompt

Usa una o más imágenes como guía visual. No requiere imagen fuente, pero sí al menos una referencia
habilitada.

Puede transferir de manera aproximada:

- Paleta y atmósfera.
- Estilo o materialidad.
- Diseño de personaje u objeto.
- Composición general.

No significa copiar píxeles ni conservar identidad exacta. Para una composición con bordes claros,
usa PyraCanny; para una estructura más flexible, CPDS; para identidad facial, FaceSwap.

Ejemplo:

```text
Prompt:
a compact electric motorcycle parked in a rainy neon street, three-quarter view,
industrial product design, realistic reflections, cinematic night photography

Referencias:
1. Image Prompt — fotografía con la paleta e iluminación deseadas
2. Image Prompt — boceto del lenguaje de diseño del vehículo
```

### Describe

Analiza una imagen fuente y devuelve una descripción para reutilizar como prompt.

Requisitos:

- Imagen fuente.
- Al menos un tipo de contenido: **Fotografía** o **Arte/Anime**.

Opciones:

- **Fotografía:** busca una descripción apropiada para imagen fotográfica.
- **Arte/Anime:** produce etiquetas o conceptos más útiles para ilustración/anime.
- **Aplicar estilos:** permite que Fooocus proponga estilos compatibles además de la descripción.

Usos:

- Entender cómo describir una imagen propia.
- Crear un punto de partida para una variación.
- Recuperar etiquetas de una ilustración.
- Documentar un lote visual antes de seleccionar referencias.

Describe no reconoce hechos con certeza. Nombres, marcas, personas y lugares pueden ser erróneos;
revisa el texto antes de usarlo.

### Enhance

Mejora regiones detectadas automáticamente y puede aplicar varias etapas secuenciales. Es la
operación más compleja del tab y se explica en una sección propia.

## Imágenes de referencia

### Agregar y administrar referencias

En **Referencias**:

- **Agregar…:** selecciona una o varias imágenes.
- **Quitar:** elimina la referencia de la cola, sin borrar el archivo original.
- **↑ / ↓:** cambia el orden.
- **Usar/no usar:** activa o desactiva temporalmente la referencia.
- **Ver:** abre una previsualización.
- Doble clic: alterna su estado de uso.

La instalación detectada ofrece cuatro ranuras. El límite se descubre desde Fooocus; no se debe
confundir con la única imagen fuente de variación/inpaint/Enhance.

La cola es transitoria: se reconstruye manualmente al reiniciar. AIOpenStudio copia únicamente las
referencias habilitadas al directorio de la ejecución.

### Tipo de referencia

#### Image Prompt

Guía semántica y estética general.

Buenos casos:

- Paleta, iluminación y ambiente.
- Diseño aproximado de un personaje.
- Materiales o lenguaje visual de producto.
- Combinar estilo de una imagen y contenido de otra.

Punto de partida: `Stop 0.5`, `Peso 0.6`.

#### PyraCanny

Extrae y usa bordes. Prioriza siluetas y contornos visibles.

Buenos casos:

- Arquitectura y fachadas.
- Pose o contorno de un objeto.
- Composición de producto.
- Convertir line art o un boceto limpio en una imagen terminada.

Punto de partida: `Stop 0.5`, `Peso 1.0`.

Un peso alto sobre una referencia con mucho ruido o textura puede copiar bordes no deseados. Limpia
el boceto o baja el peso.

#### CPDS

Conserva estructura, profundidad y distribución general con más flexibilidad que Canny.

Buenos casos:

- Mantener masas de un paisaje.
- Guiar profundidad de un interior.
- Conservar pose sin exigir cada borde.
- Reinterpretar una fotografía en otro estilo.

Punto de partida: `Stop 0.5`, `Peso 1.0`.

#### FaceSwap

Usa el rostro de referencia como guía de identidad durante la generación.

Buenos casos:

- Personaje consistente en escenas ficticias.
- Probar vestuario o iluminación con autorización.
- Mantener rasgos generales entre conceptos.

Punto de partida: `Stop 0.9`, `Peso 0.75`.

FaceSwap no garantiza identidad exacta ni reemplazo forense. Usa sólo imágenes propias o con
consentimiento y no lo presentes como evidencia de una persona real.

### Stop

**Stop At** va de 0 a 1 e indica hasta qué fracción del proceso actúa la referencia.

- Valor bajo: la referencia deja de influir antes; el modelo dispone de más libertad al final.
- Valor alto: la guía permanece durante más etapas; suele aumentar fidelidad estructural o facial.

Ajuste práctico:

1. Empieza con el valor sugerido por tipo.
2. Si se pierde la referencia, sube en pasos de 0.05–0.1.
3. Si aparecen rigidez, artefactos o copia excesiva, bájalo.

### Peso

**Weight** va de 0 a 2 y controla la intensidad de la referencia.

- `0.2–0.5`: influencia ligera.
- `0.6–1.0`: intervalo habitual.
- `1.1–1.5`: influencia fuerte; revisar artefactos.
- Cerca de 2: uso experimental, propenso a dominar el prompt.

Stop y Peso se complementan: un peso alto durante poco tiempo no equivale a un peso moderado hasta
el final.

Después de cambiar Tipo, Stop o Peso pulsa **Aplicar** para actualizar la referencia seleccionada.

### Orden de referencias

Fooocus recibe las referencias habilitadas en el orden visible. El orden puede afectar mezclas,
aunque no representa una prioridad matemática simple.

Una estrategia útil:

1. Estructura: PyraCanny o CPDS.
2. Identidad: FaceSwap, si corresponde.
3. Estética principal: Image Prompt.
4. Paleta o material secundario: Image Prompt con menor peso.

Prueba primero cada referencia por separado. Sólo después combínalas; así sabrás cuál introduce un
defecto o contradicción.

### Mezclar referencias con otras operaciones

Marca **Mezclar referencias con variación/upscale o inpaint** para combinar referencias habilitadas
con:

- Variación sutil o fuerte.
- Upscale.
- Inpaint u outpaint.

Si hay referencias habilitadas en una operación distinta de Image Prompt y la casilla está
desmarcada, la solicitud se rechaza para evitar enviar contexto por accidente.

Ejemplo: cambiar el material de un sillón sin perder su ubicación.

- Operación: Inpaint.
- Fuente: fotografía del salón.
- Máscara: sillón.
- Referencia Image Prompt: cuero verde oliva.
- Mezcla: activada.
- Prompt adicional: `an olive green leather armchair, realistic stitching and natural folds`.

## Enhance y mejora por regiones

Enhance recibe una **Imagen fuente** y ejecuta hasta tres etapas en el esquema v2.5.5 detectado.
Cada etapa encuentra una región, crea una máscara y la procesa con parámetros de inpaint.

### Controles generales de Enhance

#### Variación/upscale

Puede encadenar una de estas operaciones con Enhance:

- Sin variación/upscale.
- Variación sutil.
- Variación fuerte.
- Upscale 1,5×.
- Upscale 2×.
- Upscale rápido 2×.

#### Orden

- `before`: realiza variación/upscale antes de las etapas de mejora.
- `after`: mejora primero y realiza variación/upscale al final.

Ejemplos:

- **Upscale before:** detecta y mejora sobre una imagen ya grande; puede ayudar con objetos pequeños,
  pero cuesta más.
- **Upscale after:** corrige regiones en el tamaño original y amplía al final; suele ser un flujo
  eficiente.

#### Fuente de prompt

- `original`: cada etapa parte de los prompts generales originales.
- `last_filled`: permite reutilizar el último prompt de mejora que contenga texto.

Usa `original` para etapas independientes. Usa `last_filled` cuando varias regiones deben compartir
un mismo lenguaje visual y quieres evitar repetir el prompt.

#### Guardar sólo imagen final

Evita presentar resultados intermedios de Enhance como outputs finales. Es útil para un pipeline de
varias etapas. Desactívalo durante ajuste para comparar qué hizo cada etapa.

### Administrar etapas

- **Agregar etapa:** crea una nueva, hasta el máximo descubierto.
- **Editar…:** abre todos sus parámetros.
- **Quitar:** elimina la seleccionada.
- **Habilitada:** permite conservar la configuración sin ejecutarla.

### Parámetros de una etapa

#### Detección

Texto que identifica la región a mejorar. Con SAM se usa como guía para localizar objetos.

Ejemplos:

```text
face
hands
red jacket
ceramic cup
front wheel
window
```

Usa nombres visuales concretos. Si hay varios objetos iguales, combina descripción, color o
posición: `woman's face on the left`, `blue cup`, `front bicycle wheel`.

#### Prompt positivo

Describe cómo debe quedar la región:

```text
natural detailed eyes, realistic skin texture, soft matching light
```

#### Prompt negativo

Defectos que deben evitarse dentro de la región:

```text
plastic skin, asymmetrical eyes, extra eyelashes, oversharpening
```

#### Modelo de máscara

| Modelo | Especialidad orientativa | Uso típico |
|---|---|---|
| `sam` | Segmentación guiada por texto y GroundingDINO | Objetos nombrables y regiones específicas |
| `u2net` | Primer plano general | Separar sujeto/fondo |
| `u2netp` | Variante más ligera | Pruebas rápidas de primer plano |
| `u2net_human_seg` | Silueta humana | Persona completa |
| `u2net_cloth_seg` | Prendas | Cambiar o mejorar ropa |
| `silueta` | Silueta/primer plano | Recortes generales |
| `isnet-general-use` | Segmentación general | Objetos y sujetos variados |
| `isnet-anime` | Ilustración/anime | Personajes dibujados |

Los modelos requieren activos locales diferentes. Si falta el seleccionado, el preflight detiene
la operación y no intenta descargarlo.

#### Ropa

Sólo es relevante para `u2net_cloth_seg`:

- `full`: conjunto completo.
- `upper`: prendas superiores.
- `lower`: prendas inferiores.

#### SAM

Selecciona el tamaño de Segment Anything:

| Modelo | Característica | Recomendación |
|---|---|---|
| `vit_b` | Más ligero | Punto de partida y menor presión de memoria |
| `vit_l` | Intermedio | Más capacidad si `vit_b` no delimita bien |
| `vit_h` | Más pesado | Último recurso para máscaras complejas; mayor RAM/VRAM |

Un modelo mayor no garantiza una máscara mejor. Empieza con `vit_b`.

#### Text

Umbral de coincidencia entre palabras y regiones, de 0 a 1. Valor inicial: 0.25.

- Bájalo si no se detecta el objeto.
- Súbelo si aparecen regiones semánticamente incorrectas.

#### Box

Umbral de confianza de las cajas detectadas, de 0 a 1. Valor inicial: 0.3.

- Bájalo para recuperar objetos difíciles o pequeños.
- Súbelo para reducir falsos positivos.

Cambia Text o Box de a 0.05. Bajar ambos demasiado suele crear muchas máscaras erróneas.

#### Máx.

Número máximo de detecciones, entre 0 y 100. En esta integración `0` conserva el comportamiento de
Fooocus sin límite práctico.

- Usa `1` para un único rostro u objeto principal.
- Usa `2–4` para un pequeño grupo.
- Usa `0` sólo cuando realmente quieras procesar todas las coincidencias.

#### Inpaint

Modo aplicado a la máscara de la etapa:

- `default`: reconstrucción equilibrada.
- `detail`: mejora conservadora de detalles.
- `modify`: reemplazo o cambio más fuerte.

#### Denoise

Intensidad de regeneración de 0 a 1.

- `0.2–0.4`: conserva fuertemente el contenido original.
- `0.5–0.7`: mejora o modifica de forma moderada.
- `0.8–1.0`: reconstrucción fuerte; puede cambiar identidad y forma.

Para rostros, comienza bajo. Para reemplazar ropa u objetos, prueba valores mayores.

#### Campo

**Respective Field** controla cuánta relación contextual mantiene la región de inpaint con su
entorno. Rango 0–1; valor inicial 0.618.

- Un valor moderado ayuda a integrar luz, perspectiva y bordes.
- Un valor bajo concentra la transformación, pero puede dejar una unión visible.
- Un valor alto puede involucrar más contexto y modificar áreas cercanas.

#### Erosión

Modifica el tamaño de la máscara entre -64 y 64.

- Valor positivo: expande la zona blanca.
- Valor negativo: contrae la zona blanca.
- `0`: no la modifica.

Expande entre 4 y 12 píxeles si quedan bordes del objeto anterior. Contrae si la mejora invade una
región correcta.

#### Invertir máscara

Procesa el complemento de la máscara. Úsalo cuando deseas modificar el fondo y conservar el sujeto,
o cuando el modelo de segmentación entrega la región opuesta a la esperada.

### Ejemplo de Enhance con tres etapas

Objetivo: mejorar un retrato de moda y ampliar al final.

Configuración general:

- Operación: Enhance.
- Imagen fuente: retrato vertical.
- Variación/upscale: Upscale 1,5×.
- Orden: `after`.
- Prompt: `original`.
- Guardar sólo imagen final: activado después de ajustar.

Etapa 1, rostro:

```text
Detección: face
Positivo: natural facial detail, realistic eyes, subtle skin texture, matching soft light
Negativo: plastic skin, asymmetrical eyes, excessive makeup, oversharpening
Máscara: sam / vit_b
Text: 0.25
Box: 0.30
Máx.: 1
Inpaint: detail
Denoise: 0.35
Campo: 0.618
Erosión: 4
```

Etapa 2, chaqueta:

```text
Detección: red wool jacket
Positivo: fine red wool fibers, realistic seams, natural folds
Negativo: plastic fabric, duplicated buttons, broken zipper
Máscara: sam / vit_b
Máx.: 1
Inpaint: detail
Denoise: 0.45
Erosión: 3
```

Etapa 3, manos:

```text
Detección: hands
Positivo: anatomically natural hands, realistic fingers, matching pose and light
Negativo: extra fingers, fused fingers, duplicated hands
Máscara: sam / vit_b
Máx.: 2
Inpaint: detail
Denoise: 0.4
Erosión: 6
```

No esperes que Enhance repare toda anatomía automáticamente. Si una región está muy dañada, un
inpaint manual con máscara controlada puede ser más predecible.

## Galería, cola y archivos

### Cola FIFO

**Añadir a cola** crea un trabajo. Fooocus procesa los trabajos en orden de llegada.

Estados habituales:

- `queued`: esperando.
- `waiting_for_device`: esperando exclusividad de GPU o a otra suite.
- `starting_runtime`: iniciando el proceso aislado.
- `loading`: aplicando parámetros y cargando checkpoint.
- `generating`: generando o describiendo.
- `finalizing`: copiando y verificando resultados.
- `restoring`: restaurando LLM/Whisper suspendidos.
- `completed`, `cancelled` o `failed`.

Para cancelar, selecciona una fila y pulsa **Cancelar seleccionado**. Una tarea en cola se elimina
sin usar GPU; una tarea activa solicita cancelación fuerte y restauración de recursos.

### Galería de sesión

- Clic en una miniatura: vista ampliada.
- **◀ / ▶:** anterior o siguiente.
- **Ver:** abre el elemento seleccionado.
- **Recordar índice entre reinicios:** persiste sólo rutas y metadatos del índice.
- **Olvidar galería:** limpia índice y miniaturas, pero no borra outputs.

La galería es transitoria por defecto. Activar memoria no duplica binarios. Si un archivo fue
eliminado fuera de AIOpenStudio, se omite al reconstruir el índice.

### Entradas y privacidad

Antes de enviarlas a Fooocus, las imágenes habilitadas:

1. Se validan por extensión y contenido real.
2. Se rechazan si están dañadas, son multipágina/animadas o exceden los límites.
3. Se copian a `inputs/originals` dentro del run.
4. Se normalizan de forma segura a PNG RGB/RGBA en `inputs/normalized`.
5. Se registran por nombre, dimensiones, tamaño y SHA-256, sin guardar la ruta original del usuario.

El límite predeterminado es 256 MiB por imagen y 40 millones de píxeles.

### Outputs y metadatos

Por defecto, los runs están bajo:

```text
data/outputs/fooocus/<operation_id>/
```

Estructura aproximada:

```text
<operation_id>/
├── inputs/
│   ├── originals/
│   ├── normalized/
│   └── manifest.json
├── images/
├── events.jsonl
└── metadata.json
```

`metadata.json` conserva operación, modelo, parámetros, tiempos, estado, warnings y hashes de los
resultados. Los temporales no se presentan como outputs finales.

## Recetas prácticas

Los valores son puntos de partida, no presets garantizados. Mantén semilla fija durante el ajuste.

### 1. Retrato editorial realista

```text
Checkpoint: realisticStockPhoto_v20
Operación: Texto a imagen
Tamaño: 896×1152
Rendimiento: speed para pruebas; quality al finalizar
Guidance: 4
Sharpness: 1.5–2
Estilos: Fooocus V2, Fooocus Photograph

Prompt:
editorial portrait of an adult marine biologist on a research vessel, weathered blue jacket,
calm confident expression, overcast ocean in the background, soft natural light, realistic skin,
85mm photography, shallow depth of field, restrained blue and gray palette

Negativo:
plastic skin, heavy makeup, extra fingers, deformed hands, text, watermark, oversaturated colors
```

Si el rostro parece artificial, baja Sharpness antes de cambiar el checkpoint.

### 2. Fotografía de producto con espacio para publicidad

```text
Checkpoint: realisticStockPhoto_v20
Operación: Texto a imagen
Tamaño: 1344×768
Imágenes: 4
Guidance: 4.5
Sharpness: 3

Prompt:
premium studio photograph of a matte black fountain pen resting on a cream stone surface,
product placed in the right third, large clean copy space on the left, soft directional light,
subtle shadow, luxury editorial advertising, realistic materials, no branding

Negativo:
text, logo, watermark, extra pens, distorted clip, harsh reflections, clutter
```

Añade el texto de campaña posteriormente con software de diseño; no dependas del modelo para
tipografía exacta.

### 3. Concept art de personaje consistente

Primera ejecución:

```text
Checkpoint: animaPencilXL_v500
Operación: Texto a imagen
Tamaño: 768×1344

Prompt:
full-body character concept of an adult desert cartographer, practical layered linen clothing,
brass navigation tools, weathered satchel, standing pose, clean neutral background,
detailed anime concept art, clear silhouette, front three-quarter view
```

Después selecciona el mejor resultado y úsalo como referencia **Image Prompt**:

```text
Operación: Image Prompt
Referencia: Image Prompt, Stop 0.65, Peso 0.75
Prompt:
the same desert cartographer crossing ancient salt flats during a windstorm, dynamic full-body
pose, cinematic anime illustration, consistent clothing and brass navigation tools
```

Para mayor consistencia facial agrega una segunda referencia FaceSwap con consentimiento.

### 4. Convertir un boceto en una imagen terminada

```text
Operación: Image Prompt
Referencia 1: boceto limpio, PyraCanny, Stop 0.55, Peso 1.0
Referencia 2: imagen de paleta, Image Prompt, Stop 0.4, Peso 0.4
Tamaño: la misma orientación del boceto

Prompt:
modern timber cabin beside a mountain lake, preserve the sketch composition, realistic cedar and
glass materials, early autumn, soft fog, architectural visualization, natural diffused light

Negativo:
changed building silhouette, extra floors, crooked windows, text, watermark
```

Si el resultado copia líneas internas irrelevantes, baja el peso de PyraCanny o limpia el boceto.

### 5. Reinterpretar una fotografía como ilustración

```text
Checkpoint: animaPencilXL_v500
Operación: Image Prompt
Referencia: CPDS, Stop 0.55, Peso 0.9

Prompt:
the same street depth and arrangement, hand-painted anime background, late afternoon sunlight,
soft watercolor clouds, expressive foliage, detailed but calm atmosphere
```

CPDS conserva mejor la disposición general que los bordes exactos, permitiendo cambiar el estilo.

### 6. Eliminar una persona u objeto

```text
Operación: Inpaint
Fuente: imagen original
Máscara: área del objeto y un margen pequeño
Modo: default

Prompt general:
quiet public garden in spring, realistic photography, coherent morning light

Prompt adicional:
continuous grass and flowering shrubs matching the surrounding garden, natural shadows

Negativo:
person, object, silhouette, blur patch, repeated plants, visible seam
```

Si queda un halo, amplía la máscara. Si cambia demasiado fondo, reduce la zona.

### 7. Cambiar vestuario conservando el resto

```text
Operación: Inpaint
Modo: modify
Máscara: sólo chaqueta y bordes necesarios
Prompt adicional:
a tailored dark green wool coat, realistic fabric, brass buttons, natural folds,
matching body pose and scene lighting
Negativo:
changed face, changed hands, plastic fabric, duplicated buttons, extra clothing
```

Opcional: agrega una referencia Image Prompt de la tela y activa la mezcla con inpaint.

### 8. Reparar rostro o manos

Para una sola región problemática, usa Inpaint `detail` con máscara manual:

```text
Prompt adicional:
natural anatomically correct hand, five fingers, matching pose, matching skin tone and light

Negativo:
extra fingers, fused fingers, missing fingers, different pose, plastic skin
```

Usa máscara ajustada, Guidance moderado y evita Denoise extremo en Enhance. Genera varias opciones:
la anatomía no se resuelve de forma determinista.

### 9. Expandir un retrato para una portada

```text
Operación: Outpaint
Fuente: retrato vertical
Direcciones: top, bottom
Tamaño: 768×1344

Prompt:
full fashion portrait continuing naturally from the source, complete coat and neutral studio
floor, soft overhead light, balanced vertical magazine cover composition, clean space above head

Negativo:
text, logo, duplicated body, extra limbs, abrupt seam, different clothing
```

Si necesitas espacio sólo arriba para el título, usa únicamente `top`.

### 10. Crear un banner desde una imagen cuadrada

Primera pasada: Outpaint `left` y `right`. Segunda pasada opcional: variación sutil.

```text
Prompt:
panoramic continuation of the same misty pine forest, coherent horizon and morning light,
subject remains centered, natural empty space at both sides, cinematic landscape
```

Inspecciona el horizonte y los patrones repetidos antes de aplicar upscale.

### 11. Diseño de interiores usando varias referencias

```text
Operación: Image Prompt
Referencia 1: CPDS de la habitación, Stop 0.55, Peso 1.0
Referencia 2: Image Prompt de materiales, Stop 0.45, Peso 0.55
Referencia 3: Image Prompt de paleta, Stop 0.35, Peso 0.35

Prompt:
contemporary small living room preserving the reference layout, pale oak cabinetry, limestone
floor, olive linen sofa, warm indirect evening light, realistic architectural photography,
functional circulation, uncluttered

Negativo:
changed room geometry, blocked doorway, duplicate furniture, fisheye distortion, text
```

Deshabilita temporalmente cada referencia para medir su efecto.

### 12. Variantes de un producto conceptual

```text
Operación: Variación sutil
Fuente: diseño elegido
Semilla: fija durante cada comparación
Prompt:
same compact desk lamp design and camera angle, alternative brushed aluminum finish,
warm white diffuser, realistic studio product photography
```

Usa variación fuerte si deseas explorar otra forma, y vuelve a variación sutil para refinar la
selección.

### 13. Cambiar la atmósfera sin perder composición

```text
Operación: Image Prompt
Referencia 1: PyraCanny de la escena, Stop 0.5, Peso 0.9
Referencia 2: Image Prompt de atmósfera nocturna, Stop 0.4, Peso 0.45

Prompt:
same architecture and camera position at night after rain, warm interior windows,
blue-hour sky, realistic wet reflections, cinematic but natural lighting
```

### 14. Recrear un personaje en otra escena con FaceSwap

```text
Operación: Image Prompt
Referencia 1: FaceSwap frontal y nítida, Stop 0.9, Peso 0.75
Referencia 2: Image Prompt del vestuario, Stop 0.45, Peso 0.5

Prompt:
adult expedition leader inside an arctic research station, medium portrait, insulated red jacket,
soft practical lighting, realistic documentary photography, confident neutral expression
```

Usa una referencia facial bien iluminada y sin oclusiones. No uses imágenes de terceros sin
permiso.

### 15. Restaurar una fotografía antigua

Flujo recomendado:

1. Conserva un original sin modificar.
2. Usa Inpaint `detail` por daño localizado, no una máscara sobre toda la imagen.
3. Repara rasgaduras y manchas en ejecuciones separadas.
4. Aplica variación sutil sólo si aceptas reinterpretación.
5. Usa Upscale 1,5× al final.

```text
Prompt adicional:
continuous original photographic texture, matching grayscale grain, natural fabric and skin detail

Negativo:
modern color, plastic skin, invented jewelry, oversharpening, smooth digital texture
```

La IA inventa detalle perdido. El resultado no es una reconstrucción histórica verificable.

### 16. Generar un prompt a partir de una imagen

```text
Operación: Describe
Fuente: imagen seleccionada
Tipos: Fotografía; agrega Arte/Anime si corresponde
Aplicar estilos: activado para explorar, desactivado para descripción más neutral
```

Luego:

1. Copia la descripción del panel derecho.
2. Corrige errores y elimina etiquetas irrelevantes.
3. Pásala a Texto a imagen o Image Prompt.
4. Añade intención propia: composición, luz, uso y restricciones.

### 17. Pipeline completo de selección y acabado

1. **Texto a imagen**, `speed`, cuatro imágenes.
2. Fija la mejor semilla y corrige el prompt.
3. **Variación sutil** para alternativas cercanas.
4. **Inpaint** para defectos específicos.
5. **Enhance** para rostro, ropa u objetos localizables.
6. **Upscale 1,5× o 2×**.
7. Exporta PNG maestro y crea JPEG/WEBP para publicación en otra herramienta si hace falta.

No apliques todos los pasos por rutina. Cada transformación puede introducir cambios; detente
cuando la imagen ya cumple el objetivo.

## Diagnóstico y problemas frecuentes

### La operación no aparece

1. Pulsa **Actualizar**.
2. Comprueba que el estado muestre un esquema `live` o `cached`.
3. Ejecuta el preflight de sólo lectura:

```powershell
.\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py preflight
```

No inicia Fooocus ni descarga activos.

### Aparece “Activos Fooocus faltantes”

La operación necesita un upscaler, modelo de inpaint, adaptador, interrogador, SAM o REMBG ausente.
No intentes iniciar Fooocus manualmente para que lo descargue. Consulta:

- `models/fooocus/advanced-assets.json`
- `docs/fooocus-validation.md`

La incorporación exige revisar fuente/licencia, autorizar la descarga y registrar tamaño y SHA-256.

### Una referencia domina demasiado

- Baja Peso de 0.1 en 0.1.
- Baja Stop.
- Desactiva otras referencias.
- Simplifica el prompt y elimina estilos contradictorios.

### La referencia casi no influye

- Sube Peso o Stop gradualmente.
- Usa PyraCanny para bordes o CPDS para estructura.
- Recorta la referencia para que el sujeto ocupe más área.
- Verifica que la referencia esté marcada con `☑` y hayas pulsado **Aplicar**.

### Inpaint deja bordes

- Amplía ligeramente la máscara.
- Describe textura, luz y material circundante.
- Usa `default` para continuidad y `detail` para corrección conservadora.
- Procesa una región a la vez.

### Enhance no detecta el objeto

- Simplifica el texto de Detección.
- Baja Text o Box en pasos de 0.05.
- Cambia SAM `vit_b` por `vit_l` sólo después de ajustar umbrales.
- Usa máscara manual con Inpaint si necesitas control exacto.

### Enhance modifica demasiado

- Baja Denoise.
- Usa modo `detail`.
- Reduce Erosión o aplica un valor negativo.
- Limita Máx. a 1.
- Reduce Guidance o la fuerza de prompts regionales.

### El resultado no es reproducible

Comprueba que sean iguales:

- Checkpoint y archivo exacto.
- Seed.
- Prompt y negativo.
- Estilos y orden.
- Tamaño, rendimiento, Guidance y Sharpness.
- Fuentes, máscaras, referencias, orden, Stop y Peso.
- Versión de Fooocus y entorno.

### El trabajo espera GPU

AIOpenStudio serializa las cargas pesadas. Fooocus puede esperar a que LLM o Whisper terminen,
suspender residentes administrados y restaurarlos después. No cierres procesos a mano mientras la
aplicación coordina la tarea.

### Cancelé, pero tardó unos segundos

Una cancelación activa primero solicita detener el trabajo y luego puede cerrar el proceso aislado
para garantizar la liberación. La restauración de otras suites también forma parte del cierre.

### La galería quedó vacía al reiniciar

Es el comportamiento predeterminado. Activa **Recordar índice entre reinicios** antes de cerrar.
Los outputs continúan en disco aunque la galería se olvide.

## Límites y uso responsable

- Revisa licencias de checkpoints y activos antes de publicar o redistribuir resultados.
- No uses FaceSwap con personas sin consentimiento ni para suplantación, fraude o desinformación.
- No presentes una restauración generativa como registro histórico fiel.
- No uses outputs como prueba factual o biométrica.
- Revisa sesgos, anatomía, símbolos, texto y detalles antes de publicar.
- Mantén originales, semillas y metadatos cuando la trazabilidad importe.
- Las imágenes generadas pueden parecer marcas, obras o personas existentes; realiza revisión legal
  cuando el uso sea comercial o de alto impacto.

## Lecturas relacionadas

- [Capacidades avanzadas y privacidad](fooocus-advanced-capabilities.md)
- [Validación y preflight](fooocus-validation.md)
- [Compatibilidad de entorno y memoria](environment-compatibility.md)
- [Solución general de problemas](troubleshooting.md)
- [Plan del proyecto](PLAN.md)
