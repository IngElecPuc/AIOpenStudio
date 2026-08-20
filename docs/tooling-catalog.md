# Catálogo inicial de herramientas y modelos de IA

Inventario de candidatos revisado el **20 de agosto de 2026**. Este documento es una
evaluación previa: **ninguna herramienta o modelo nuevo queda aprobado por aparecer aquí**.
No se clonaron repositorios, no se instalaron paquetes y no se descargaron pesos durante
esta investigación.

## Restricciones de selección

La referencia de hardware es la descrita en
[`environment-compatibility.md`](environment-compatibility.md): Windows 11, Python 3.12,
PyTorch 2.11 con CUDA 12.8, RTX 5060 Laptop con 8.151 MiB de VRAM y 32 GiB de RAM.

- Se reserva al menos 1,25 GiB de VRAM para Windows, la UI, el contexto y temporales.
- Se admite inicialmente una sola carga pesada en GPU y una sola petición en paralelo.
- Q4_K_M es el perfil normal para modelos de 7B/8B.
- Q5 solo se probará si el archivo ronda 5,5 GB o menos, con contexto de 2K o 4K y
  telemetría activa. No se presupone que quepa por el tamaño del archivo.
- Los contextos anunciados de 32K, 128K o más no son objetivos para este equipo. La caché
  KV crece con el contexto y puede agotar la VRAM aunque los pesos entren.
- RAM, VRAM, tamaño en disco y memoria temporal son mediciones distintas.
- Los runtimes se enlazarán a loopback y los datos permanecerán locales por defecto.
- Cada descarga futura debe registrar fuente oficial, versión o digest, licencia, tamaño y
  checksum cuando la fuente lo proporcione.

## Estados del catálogo

| Estado | Significado |
|---|---|
| Base existente | Ya forma parte de la base acordada o de sus dependencias |
| Propuesto | Primera opción para un experimento acotado, todavía sin aprobación |
| Candidato | Alternativa viable que merece conservarse en el catálogo |
| Diferido | No aporta suficiente valor ahora o excede los recursos previstos |
| Descartado por ahora | Incompatible con una restricción actual; se puede reevaluar |

No hay candidatos nuevos en estado “aprobado”. Por eso aún no corresponde crear decisiones
en `docs/decisions/` para ellos.

### Confianza cualitativa

La confianza indicada en las categorías exploratorias no es una puntuación matemática ni una
medida de calidad absoluta. Resume señales públicas combinadas: procedencia oficial, claridad de
licencias de código y pesos, actividad reciente, releases reproducibles, documentación, adopción
por otras herramientas y existencia de benchmarks o implementaciones independientes.

- **Alta:** proyecto oficial o mantenido, licencia clara, adopción amplia y ruta de ejecución
  suficientemente probada.
- **Media-alta:** señales técnicas y comunitarias fuertes, pero modelo reciente, compatibilidad
  local no medida o alguna dependencia que exige aislamiento.
- **Media:** opción útil con mantenimiento reducido, licencia particular, pesos heterogéneos o
  evidencia todavía insuficiente para convertirla en dependencia.

Una confianza alta no constituye aprobación ni garantiza que un modelo quepa en este equipo.

## Runtime de LLM

| Opción | Licencia y actividad | API e integración | Windows/CUDA y recursos | Privacidad, actualización y estado |
|---|---|---|---|---|
| **Ollama** | MIT; proyecto y documentación activos | API HTTP local y cliente Python oficial; `keep_alive=0` libera un modelo de memoria y un valor negativo lo mantiene cargado | Soporte nativo para Windows y selección automática de GPU; permite limitar modelos y paralelismo | Enlazar a `127.0.0.1:11434`, activar `OLLAMA_NO_CLOUD`, fijar tags/digests y evitar `pull` implícito. **Base existente / propuesto como runtime primario** |
| **llama.cpp / llama-server** | MIT; lanzamientos frecuentes | Servidor HTTP compatible con OpenAI, CLI y GGUF; permite controlar capas en GPU y CPU | Binarios Windows con CUDA y backends CUDA, Vulkan, SYCL, HIP y CPU; útil para offload parcial | Puede descargar desde Hugging Face si se usa `-hf`, función que AIOpenStudio no debe invocar sin aprobación. **Candidato como runtime secundario** |

Configuración inicial propuesta para Ollama: `OLLAMA_MAX_LOADED_MODELS=1`,
`OLLAMA_NUM_PARALLEL=1`, contexto de 4K, `OLLAMA_GPU_OVERHEAD` alineado con la reserva local,
`OLLAMA_HOST=127.0.0.1:11434` y `OLLAMA_NO_CLOUD=true`. Ollama documenta que el paralelismo
multiplica el contexto efectivo y, por tanto, la memoria necesaria. También permite liberación
explícita con `ollama stop` o `keep_alive=0`. Fuentes: [FAQ de Ollama](https://docs.ollama.com/faq),
[configuración del servidor](https://github.com/ollama/ollama/blob/main/envconfig/config.go) y
[API de generación](https://docs.ollama.com/api/generate).

`llama.cpp` conserva valor arquitectónico porque expone GGUF directamente, admite varios
backends y ofrece un servidor HTTP desacoplado. No conviene incorporarlo junto con Ollama en
la primera vertical: duplicaría instalación, catálogo y reglas de residencia antes de validar
el contrato común. Fuente: [repositorio oficial de llama.cpp](https://github.com/ggml-org/llama.cpp).

## Modelos LLM candidatos

Los tamaños siguientes son los artefactos publicados por Ollama, no promesas de consumo de
VRAM. Todos se ejecutarían mediante Ollama en la primera evaluación.

La columna de thinking expresa el **máximo control confirmado**, no la calidad de razonamiento del
modelo. `No permite` significa que Ollama no anuncia la capacidad `thinking`; `true` representa un
control booleano `false/true`; `high` implicaría `low/medium/high`; y `max` implicaría todos los
niveles inferiores además de `max`. Un modelo descrito comercialmente como bueno para razonar no
se marca como thinking si no expone el campo separado y controlable de Ollama.

| Modelo y variante | Uso principal | Thinking máximo confirmado | Licencia | Archivo y contexto publicado | Encaje estimado en 8 GB | Actividad y estado |
|---|---|---|---|---|---|---|
| **Qwen3 8B Q4_K_M** | General, español, herramientas y razonamiento con modo thinking | **`true`** (`false/true`) | Apache-2.0 | 5,2 GB; 40K | Viable con 4K, una petición y sin otra carga GPU; es el límite alto cómodo | Publicado hace alrededor de un año. **Propuesto como LLM general inicial** |
| **Qwen2.5-Coder 7B Instruct Q4_K_M** | Código, explicación, corrección y FIM | **No permite** | Apache-2.0 | 4,7 GB; 32K. Q5_K_M: 5,4 GB | Q4 viable; Q5 solo como comparación posterior de 2K/4K | Familia estable de hace alrededor de un año. **Propuesto para código** |
| **DeepSeek-R1 Distill Qwen 7B Q4_K_M** | Razonamiento y comparación con Qwen general | **`true`** (`false/true`) | MIT sobre base Qwen Apache-2.0 | 4,7 GB; 128K | Viable con contexto restringido; las respuestas de razonamiento pueden elevar latencia y tokens | Familia estable de hace alrededor de un año. **Candidato de benchmark** |
| **Granite 4 7B-A1B-H Q4_K_M** | Herramientas, RAG, diálogo multilingüe y código | **No permite** | Apache-2.0 | 4,2 GB; arquitectura híbrida con contexto publicado de 1M | Buen margen, pero el backend híbrido debe probarse; nunca usar 1M en este equipo | Lanzado en 2025 y publicado en Ollama hace menos de un año. **Candidato eficiente** |
| **Phi-4-mini 3.8B Q4_K_M** | Control liviano, herramientas y tareas cortas | **No permite** | MIT | 2,5 GB; 128K | Amplio margen; útil para validar integración antes de un 7B/8B | Familia estable de hace alrededor de un año. **Propuesto como smoke test** |
| **Gemma 3 4B Q4_K_M** | Texto e imagen en un modelo compacto | **No permite** | Términos de Uso de Gemma, con obligaciones y restricciones propias | 3,3 GB; 128K; entrada de texto e imagen | Buen margen; requiere revisión de licencia antes de aprobar | Familia estable de hace alrededor de un año. **Candidato multimodal** |
| **Gemma 4 E4B Instruct QAT** | Razonamiento, agentes, herramientas y comprensión multimodal | **`true`**; niveles pendientes de validación local | Apache-2.0 | 6,1 GB; 128K; QAT con archivo Q6_K; texto e imagen en Ollama | Candidato viable pero ajustado: usar contexto corto, una sola carga y telemetría activa | Lanzado en 2026. **Incorporado al catálogo para evaluación local** |
| **Llama 3.1 8B Instruct Q4_K_M** | Línea base general ampliamente soportada | **No permite** | Meta Llama 3.1 Community License | 4,9 GB; 128K | Viable con contexto corto | Madura, pero la licencia es menos simple que Apache/MIT. **Candidato secundario** |
| **Mistral 7B Instruct v0.3 Q4_K_M** | Línea base general y function calling | **No permite** | Apache-2.0 | 4,4 GB; 32K | Viable y conservador | Modelo maduro, menos reciente. **Candidato de regresión** |
| **Qwen3.5 9B Q4_K_M** | Modelo general/multimodal más reciente | **`true`** (`false/true`); niveles no confirmados | Apache-2.0 | 6,6 GB; 256K | Demasiado ajustado tras reservar VRAM para KV y temporales | Activo y reciente. **Diferido por memoria** |

Ollama acepta en su API general `false`, `true`, `low`, `medium`, `high` y `max`, pero cada modelo
puede implementar sólo un subconjunto. La documentación oficial identifica a Qwen 3 y DeepSeek R1
como modelos booleanos; reserva niveles graduados explícitos para familias que los soportan. En el
parser actual de Qwen3.5, los valores se reducen a activado/desactivado, por lo que no se atribuyen
niveles reales sin una prueba por versión. Antes de exponer el control en la UI se deberá consultar
`/api/show`, comprobar la versión local de Ollama y realizar una inferencia acotada por cada tag.

Fuentes de tamaños y metadatos: [Qwen3](https://ollama.com/library/qwen3/tags),
[Qwen2.5-Coder](https://ollama.com/library/qwen2.5-coder/tags),
[DeepSeek-R1](https://ollama.com/library/deepseek-r1/tags),
[Granite 4](https://ollama.com/library/granite4),
[Phi-4-mini](https://ollama.com/library/phi4-mini/tags),
[Gemma 3](https://ollama.com/library/gemma3/tags),
[Gemma 4](https://ollama.com/library/gemma4/tags),
[Llama 3.1](https://ollama.com/library/llama3.1/tags),
[Mistral 7B](https://ollama.com/library/mistral/tags) y
[Qwen3.5 9B](https://ollama.com/library/qwen3.5%3A9b).
El contrato y la semántica de thinking se contrastaron con la
[documentación oficial de thinking](https://docs.ollama.com/capabilities/thinking),
[la API de chat](https://docs.ollama.com/api/chat),
[el esquema OpenAPI oficial](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml) y
[el parser oficial de Qwen3.5](https://github.com/ollama/ollama/blob/main/model/parsers/qwen35.go).
Las licencias de los candidatos preferidos se contrastaron además con las tarjetas oficiales de
[Qwen3](https://huggingface.co/Qwen/Qwen3-8B),
[Qwen2.5-Coder](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct),
[DeepSeek-R1 Distill](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B) y
[Granite 4](https://github.com/ibm-granite/granite-4.0-language-models).

### Orden de evaluación recomendado

1. Phi-4-mini 3.8B Q4 para validar instalación, streaming, cancelación y carga/descarga.
2. Qwen3 8B Q4 como modelo general principal.
3. Qwen2.5-Coder 7B Q4 para la especialidad de código.
4. Granite 4 7B-A1B-H Q4 o DeepSeek-R1 7B Q4 como comparación, no ambos a la vez.
5. Gemma 4 E4B Instruct QAT como prueba multimodal y de thinking con contexto inicial de 2K/4K.
6. Solo si Q4 deja margen medido: Qwen2.5-Coder Q5 a 2K/4K para cuantificar si la mejora
   compensa latencia y presión de memoria.

## Voz y variantes de Whisper

| Opción | Licencia y actividad | API y capacidades | Windows/CUDA, memoria y dependencias | Privacidad, actualización y estado |
|---|---|---|---|---|
| **faster-whisper** | MIT; actividad visible en 2026 | API Python con CTranslate2, VAD y transcripción por lotes | Python 3.9+; GPU requiere CUDA 12, cuBLAS y cuDNN 9. Su benchmark oficial en una RTX 3070 Ti de 8 GB reporta ~2,9 GB con large-v2 int8 sin batch y ~4,5 GB con batch 8 | Local después de descargar pesos; aislar versiones de CTranslate2/cuDNN. **Propuesto como backend Whisper inicial** |
| **OpenAI Whisper** | Código y pesos MIT; implementación de referencia | API Python sencilla, traducción y varios tamaños oficiales | Comparte PyTorch del proyecto y necesita FFmpeg. La tabla oficial estima ~5 GB para medium, ~6 GB para turbo y ~10 GB para large | Local; integración directa pero más lenta y con más memoria que alternativas optimizadas. **Candidato de referencia/fallback** |
| **whisper.cpp** | MIT; lanzamientos activos en 2026 | CLI, biblioteca C/C++, cuantización y servidor de ejemplo | Windows, CPU, CUDA y Vulkan; requiere binario compatible o compilación. La ruta CUDA para RTX 50 debe validarse específicamente | Muy aislable como subproceso y sin entorno Python pesado. **Candidato de fallback portable** |
| **WhisperX** | BSD-2-Clause; actividad visible en 2026 | Faster-whisper más timestamps por palabra, alineación y diarización | CUDA 12.8; añade modelos de alineación, VAD y diarización. Para diarización exige token de Hugging Face y aceptar términos del modelo; aumenta memoria y complejidad | Adecuado solo cuando el caso de uso necesite hablantes/timestamps precisos. **Diferido como extensión opcional** |

La primera prueba debería usar `faster-whisper` con `small` o `medium`, `compute_type=int8`,
batch 1 y un audio local corto en español. `large-v3`/`turbo`, batching y diarización se evalúan
después y nunca mientras haya un LLM o Fooocus residente en GPU. La suite debe liberar el modelo
al terminar o ante una solicitud del coordinador de recursos.

Fuentes: [Whisper oficial](https://github.com/openai/whisper),
[faster-whisper y sus benchmarks](https://github.com/SYSTRAN/faster-whisper),
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) y
[WhisperX](https://github.com/m-bain/whisperX).

## Síntesis de voz (TTS), exploración futura

Whisper no sintetiza audio. Esta categoría queda registrada para una extensión posterior y no
modifica los contratos, dependencias ni alcance de la suite Whisper actual. En particular, la
clonación de voz requerirá consentimiento verificable sobre el audio de referencia, procedencia
registrada y una señal visible en la UI que distinga voces incorporadas de voces clonadas.

| Modelo o implementación | Capacidades y licencia | Encaje en español y 8 GB | Señales de confianza | Estado futuro |
|---|---|---|---|---|
| **Qwen3-TTS 0.6B Base / CustomVoice** | Código y pesos Apache-2.0; streaming, voces incorporadas y clonación desde una referencia breve. La familia 1.7B añade diseño de voz e instrucciones expresivas | Incluye español entre 10 idiomas. El modelo 0.6B es el candidato natural para medir primero; FlashAttention 2 es recomendado y puede complicar Windows, por lo que necesita entorno aislado y medición real | Equipo oficial Qwen, desarrollo activo en 2026, paquete Python y documentación extensa; tecnología todavía reciente. **Media-alta** | **Candidato SOTA compacto** |
| **Chatterbox Multilingual V3 500M** | Código y pesos MIT; TTS expresivo, clonación zero-shot, control y watermark PerTh | Soporta 23 idiomas y ofrece modelos dedicados para español de España y español latinoamericano. El repositorio se probó oficialmente en Python 3.11/Debian, no en nuestro stack Windows; 500M parece razonable, pero no hay presupuesto oficial de VRAM que permita darlo por hecho | Proyecto oficial de Resemble AI, adopción comunitaria alta, paquete publicado y tercera iteración multilingüe. **Alta en ecosistema; media-alta en compatibilidad local** | **Propuesto como primer experimento expresivo futuro** |
| **Kokoro-82M** | Pesos Apache-2.0, aproximadamente 327 MB; voces predefinidas y síntesis muy liviana, sin clonación zero-shot equivalente a Qwen/Chatterbox | Incluye tres voces españolas mediante `espeak-ng`. Es el candidato más seguro para CPU y para una primera integración con poca presión de GPU; se debe evaluar naturalidad y pronunciación chilena | Modelo muy difundido, pequeño, licencia clara y superficie simple; la calidad en español tiene menos evidencia que en inglés. **Alta como baseline eficiente** | **Propuesto como fallback liviano** |
| **Piper (OHF-Voice)** | Motor local GPL-3.0 con CLI, servidor y API Python/C++; voces pequeñas y ejecución rápida en CPU. Cada voz puede tener licencia propia y debe catalogarse por separado | Amplia selección de idiomas y voces, adecuado para lectura determinista y accesibilidad; no busca clonación ni expresividad SOTA | Releases activas en 2026 y adopción documentada por Home Assistant, NVDA y proyectos de voz local. **Alta como motor estable** | **Candidato de producción CPU** |
| **F5-TTS** | Código MIT, pero los pesos oficiales principales son CC-BY-NC por el dataset Emilia; flow matching, clonación y generación multivoz | Existe un checkpoint comunitario español declarado CC0, pero exige una auditoría independiente de datos, calidad y procedencia. El flujo oficial puede cargar ASR adicional si no se proporciona la transcripción de referencia | Repositorio y paper sólidos, pero la licencia no comercial de los pesos principales y la fragmentación por idioma reducen su encaje. **Media** | **Diferido por licencia/procedencia** |
| **Coqui XTTS-v2** | Código MPL-2.0 y pesos bajo Coqui Public Model License, restringidos a uso no comercial; clonación y 16 idiomas | Español y clonación maduros, pero la licencia del modelo no se volvió permisiva tras el cierre de la empresa. El repositorio original dejó de ser una base de mantenimiento confiable aunque exista un fork comunitario | Mucha adopción histórica, pero gobernanza y licencia de pesos problemáticas. **Media histórica, baja para incorporación nueva** | **Descartado por ahora** |

Orden de evaluación futuro recomendado: Kokoro para validar el contrato y la liberación de
recursos; Chatterbox Multilingual V3 para calidad/clonación en español; Qwen3-TTS 0.6B como
comparación SOTA con streaming. Piper queda como fallback de CPU. Ninguna voz clonada debe
guardarse o reutilizarse sin metadatos de consentimiento y finalidad.

Fuentes: [Qwen3-TTS oficial](https://github.com/QwenLM/Qwen3-TTS),
[Chatterbox oficial](https://github.com/resemble-ai/chatterbox),
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
[voces de Kokoro](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md),
[Piper mantenido por OHF-Voice](https://github.com/OHF-Voice/piper1-gpl),
[F5-TTS](https://github.com/SWivid/F5-TTS) y
[documentación de XTTS-v2](https://github.com/coqui-ai/TTS/blob/dev/docs/source/models/xtts.md).

## Separación de fuentes musicales, exploración futura

El objetivo sería recibir WAV, FLAC, MP3 u otro formato soportado y producir stems nuevos sin
alterar el archivo original. FFmpeg debe decodificar formatos comprimidos y los resultados deben
conservar modelo, revisión, parámetros, duración, sample rate y rutas de salida. La separación no
elimina obligaciones de copyright sobre la canción ni concede derechos sobre los stems.

| Modelo o implementación | Capacidades y licencia | Windows/CUDA y recursos | Señales de confianza | Estado futuro |
|---|---|---|---|---|
| **python-audio-separator** | Wrapper MIT con CLI y API Python para MDX-Net, VR, Demucs y MDXC/RoFormer; acepta WAV, MP3, FLAC y M4A | Python 3.10+, PyTorch/ONNX Runtime, CUDA y fallback CPU. Puede descargar modelos automáticamente: AIOpenStudio deberá desactivar esa conducta o convertirla en preflight aprobado | Releases frecuentes hasta 2026, API integrable y cobertura de las familias usadas por UVR. **Alta como implementación**, no como garantía sobre cada peso | **Propuesto como adaptador inicial futuro** |
| **BS-RoFormer / Mel-Band RoFormer** | Separación de alta calidad, especialmente vocal/instrumental; papers reportan resultados SOTA en MUSDB18HQ. Código de las implementaciones comunes MIT; la licencia debe verificarse para cada checkpoint | Viables con procesamiento por segmentos y batch 1, pero el pico depende fuertemente del checkpoint, longitud de segmento y overlap. Debe probarse en 8 GB antes de admitirlo | Arquitecturas publicadas, incorporadas por UVR, audio-separator y el repositorio activo de ZFTurbo. La procedencia de pesos comunitarios es desigual. **Media-alta con checkpoint fijado** | **Propuesto para calidad vocal/instrumental** |
| **MDX-Net / MDX23C** | Modelos espectrales eficientes para dos o cuatro stems; variantes PyTorch y ONNX | Generalmente más livianos y rápidos que RoFormer; buena primera prueba de GPU/CPU y útil para lotes. Configuración y atribución dependen del peso elegido | Uso prolongado en UVR y wrappers populares, múltiples checkpoints especializados. **Alta como familia madura; media por heterogeneidad de pesos** | **Propuesto para baseline rápido** |
| **HT Demucs v4** | MIT; separación estable de voces, batería, bajo y otros, con variante experimental de seis fuentes | Compatible con Windows y CUDA, pero su stack original es antiguo. Es razonable como modelo de regresión, no como base nueva de dependencias | Referencia ampliamente adoptada y benchmark publicado; el repositorio de Meta fue archivado en enero de 2025 y su fork solo recibe correcciones importantes. **Alta histórica, media de mantenimiento** | **Candidato de referencia/fallback** |
| **Ultimate Vocal Remover (UVR)** | GUI MIT que reúne MDX, Demucs y modelos RoFormer, con instalador Windows y selección de modelos | Excelente herramienta manual para comparar resultados antes de implementar. No ofrece una frontera programática tan estable como audio-separator y gestiona descargas propias | Adopción pública muy amplia y continuidad comunitaria; mezcla modelos de autores/licencias diferentes. **Alta como referencia manual** | **Herramienta de validación, no dependencia interna** |
| **MVSep Mega 53 Stems** | BS-RoFormer multifuente para instrumentos específicos; release y pesos en ZFTurbo | El autor recomienda al menos 16 GB de VRAM y advierte menor calidad que modelos especializados en algunas fuentes; no cabe en el presupuesto inicial | Proyecto activo y transparente sobre limitaciones, pero demasiado nuevo y pesado. **Media** | **Descartado para este equipo** |

El primer experimento futuro debería comparar un MDX eficiente y un BS-RoFormer de dos stems
sobre tres fragmentos cortos y legalmente utilizables: voz central clara, mezcla densa y música con
reverberación. Las métricas mínimas serían tiempo, VRAM pico, bleed vocal/instrumental, artefactos
y conservación de fase/longitud. UVR serviría como referencia auditiva externa con los mismos
checkpoints. No se debe descargar el catálogo completo de modelos.

Fuentes: [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator),
[Music Source Separation Training de ZFTurbo](https://github.com/ZFTurbo/Music-Source-Separation-Training),
[BS-RoFormer](https://arxiv.org/abs/2309.02612),
[Mel-Band RoFormer](https://arxiv.org/abs/2310.01809),
[Demucs v4](https://github.com/facebookresearch/demucs) y
[Ultimate Vocal Remover](https://github.com/HundredBillion/UltimateVocalRemover).

## Imagen y mecanismo de integración de Fooocus

| Opción | Licencia y actividad | API e integración | Windows/CUDA y recursos | Privacidad, actualización y estado |
|---|---|---|---|---|
| **Fooocus 2.5.5** | GPL-3.0; soporte LTS limitado a correcciones, última release oficial en 2024; basado exclusivamente en SDXL | UI Gradio. La integración programática usa controles Gradio/WebSockets, no una REST estable; cambios de UI pueden romperla | Oficialmente admite Windows/NVIDIA desde 4 GB de VRAM y 8 GB de RAM con swap. Su stack PyTorch antiguo debe probarse con RTX 5060/Blackwell | Offline tras las descargas. El primer arranque descarga modelos automáticamente y el inpaint agrega 1,28 GB: debe bloquearse esa conducta hasta aprobación. **Propuesto por requisito del proyecto, con riesgo alto de integración** |
| **ComfyUI** | GPL-3.0; proyecto muy activo, releases en 2026 | Backend/API y cola asíncrona orientados a workflows | Windows y varios proveedores GPU; gestión dinámica de VRAM y opciones de reserva/offload | Puede operar local; los nodos de API externos se pueden desactivar. **Candidato futuro y alternativa técnica a Fooocus** |

### Checkpoints Fooocus instalados

Los tres archivos presentes en la biblioteca son exactamente los checkpoints que Fooocus 2.5.5
declara para sus presets oficiales. No son modelos creados por Fooocus ni el SDXL 1.0 base de
Stability AI: son *fine-tunes* SDXL comunitarios seleccionados y redistribuidos desde los mirrors
que indican los propios presets. El prefijo `image.fooocus-` del catálogo de AIOpenStudio identifica
el runtime y no forma parte del nombre original del modelo.

| Archivo local original | Identidad en AIOpenStudio | Preset oficial | Especialidad práctica | Cuándo elegirlo |
|---|---|---|---|---|
| `juggernautXL_v8Rundiffusion.safetensors` | `image.fooocus-juggernaut-xl-v8` | General | Modelo SDXL polivalente con orientación fotográfica/cinemática; es el punto de partida más equilibrado cuando el resultado puede mezclar personas, objetos, paisajes o arte conceptual | Primera prueba, escenas generales, composición cinematográfica y prompts cuyo estilo todavía no está decidido |
| `realisticStockPhoto_v20.safetensors` | `image.fooocus-realistic-stock-photo-v20` | Realistic | Fotografía realista tipo stock/editorial. El preset refuerza fotografía y excluye dibujo, anime, render y saturación mediante estilos, prompt negativo y una LoRA fotográfica | Retratos naturales, producto, interiores, arquitectura, gastronomía y escenas cotidianas que deban parecer fotografiadas |
| `animaPencilXL_v500.safetensors` | `image.fooocus-anima-pencil-xl-v500` | Anime | Ilustración anime/manga y acabado dibujado o semirrealista. El preset usa mayor guidance y estilos `Semi Realistic` y `Masterpiece` | Personajes, concept art estilizado, ilustración y estética anime; no es la primera opción para fotografía creíble |

Estas especialidades son preferencias, no límites rígidos. Los estilos, prompt negativo, semilla,
guidance y la imagen de referencia pueden cambiar sustancialmente el resultado. Para una comparación
reproducible se debe mantener el mismo prompt, semilla, proporción y perfil de rendimiento, generar
una imagen por checkpoint y juzgar anatomía, fidelidad al prompt, composición y artefactos. Las
licencias de uso deben verificarse en la ficha original de cada checkpoint antes de distribuir pesos
o explotar resultados comercialmente; que Fooocus y AIOpenStudio sean software libre no concede
automáticamente una licencia uniforme sobre los tres modelos.

### Imágenes de referencia y transformaciones

Fooocus upstream permite usar imágenes locales para mucho más que texto-a-imagen:

- variaciones sutiles o fuertes de una imagen y escalado 1,5x/2x;
- inpaint con máscara y outpaint para corregir o extender bordes;
- `Image Prompt` para transferir composición, contenido o estilo, con modos `ImagePrompt`,
  `PyraCanny`, `CPDS` y `FaceSwap`;
- `Describe` para obtener una descripción o etiquetas a partir de una imagen;
- `Enhance` para detectar y volver a generar regiones u objetos con más detalle;
- mezcla de varias referencias, prompts ponderados, estilos, LoRAs y embeddings compatibles con
  SDXL.

La integración actual de AIOpenStudio **todavía no expone estas funciones**: el contrato y el tab
Fooocus implementan texto-a-imagen, prompt negativo, checkpoint, estilos, dimensiones, seed,
guidance, nitidez, cantidad, cola, progreso, cancelación y galería. Añadir una imagen al prompt en la
UI actual no la enviará como contexto. La siguiente ampliación debe incorporar entradas y máscaras
como artefactos copiados por run, tipar el modo de transformación en el contrato, descubrir el
esquema Gradio real, validar previsualizaciones/cancelación y catalogar previamente los activos
adicionales. En particular, el inpaint oficial necesita un modelo auxiliar de aproximadamente
1,28 GB en su primer uso; la política local prohíbe que Fooocus lo descargue implícitamente.

### Frontera propuesta para Fooocus

1. Ejecutarlo como **proceso supervisado en un entorno y directorio propios**, nunca importarlo
   dentro del proceso Tkinter ni compartir automáticamente el entorno Python principal.
2. Iniciar `launch.py` sin actualizador automático, enlazado a loopback, con rutas explícitas
   para modelos y resultados.
3. Consultar salud, capturar `stdout`/`stderr`, detectar el puerto y terminar el árbol de procesos
   de forma limpia.
4. Encapsular el cliente Gradio/WebSocket detrás del contrato de runtime. La UI de AIOpenStudio
   no debe conocer parámetros de Gradio.
5. Rechazar descargas implícitas mediante un preflight que liste archivos faltantes y solicite
   autorización antes de iniciar Fooocus.
6. No adoptar un fork REST de terceros hasta revisar mantenimiento, licencia, procedencia y
   divergencia respecto del repositorio oficial.

Esta frontera reduce conflictos de PyTorch y contiene la inestabilidad de la interfaz. El propio
proyecto advierte que su acceso programático depende de controles de la UI y WebSockets. Fuentes:
[README y requisitos oficiales de Fooocus](https://github.com/lllyasviel/Fooocus),
[preset General](https://github.com/lllyasviel/Fooocus/blob/main/presets/default.json),
[preset Realistic](https://github.com/lllyasviel/Fooocus/blob/main/presets/realistic.json),
[preset Anime](https://github.com/lllyasviel/Fooocus/blob/main/presets/anime.json),
[variación y upscale](https://github.com/lllyasviel/Fooocus/discussions/390),
[Enhance](https://github.com/lllyasviel/Fooocus/discussions/3281),
[discusión oficial sobre la API](https://github.com/lllyasviel/Fooocus/discussions/2772) y
[repositorio oficial de ComfyUI](https://github.com/Comfy-Org/ComfyUI).

## Generación de video, exploración futura

La generación local de video es considerablemente más costosa que SDXL: la resolución, cantidad
de frames, VAE, encoder de texto y offload pueden dominar VRAM, RAM y tiempo. Los valores oficiales
son puntos de partida, no garantías para una RTX 5060 Laptop de 8 GB. Todo candidato debe ejecutarse
en proceso y entorno aislados, con salida incremental, cancelación y límites de duración.

| Modelo o implementación | Capacidades y licencia | Encaje en 8 GB | Señales de confianza | Estado futuro |
|---|---|---|---|---|
| **FramePack + HunyuanVideo** | FramePack Apache-2.0 empaqueta contexto a tamaño constante para predicción progresiva; usa pesos HunyuanVideo bajo licencia comunitaria de Tencent y se orienta especialmente a image-to-video largo | El repositorio oficial declara Windows, RTX 30/40/50 y mínimo 6 GB incluso con el modelo 13B. En una GPU laptop la generación será muy lenta; requiere revisar por separado las restricciones territoriales y de uso de los pesos Tencent | Proyecto del autor de Fooocus, amplia adopción pública, publicación técnica y feedback visual incremental. **Alta para viabilidad técnica; media por licencia de pesos** | **Propuesto como primer experimento de video** |
| **LTX-Video 0.9.8 2B Q8** | Pipeline rápido de texto/imagen a video; código Apache-2.0, integración oficial con ComfyUI y Diffusers. Debe fijarse la licencia del checkpoint concreto | La implementación Q8 enlazada por el proyecto reporta 720×480×121 en menos de un minuto sobre RTX 4060 de 8 GB. Es una variante anterior: LTX-2 es la línea actual y su aplicación local pide al menos 16 GB | Repositorio oficial activo y ecosistema ComfyUI sólido; la variante compatible está parcialmente reemplazada por modelos nuevos. **Media-alta** | **Candidato eficiente de comparación** |
| **Wan2.1 T2V 1.3B** | Apache-2.0; texto a video 480p y familia con múltiples tareas | El requisito oficial de 8,19 GB ya supera el margen práctico local. LightX2V anuncia ejecución optimizada en RTX 4060 de 8 GB, pero agrega otra implementación que debe auditarse | Familia oficial muy adoptada y con benchmarks publicados; ajuste de memoria extremadamente estrecho. **Alta como modelo, media para este equipo** | **Candidato solo con cuantización/offload medidos** |
| **CogVideoX 2B INT8** | Código y modelo 2B Apache-2.0; texto a video mediante Diffusers. Los pesos 5B usan una licencia propia | La documentación de Diffusers sitúa 2B INT8 cerca de 7,8 GB con optimizaciones, sin margen seguro para UI o procesos vecinos; además espera prompts en inglés y sacrifica velocidad | Proyecto oficial maduro, integración mantenida en Diffusers y abundante uso comunitario. **Alta como baseline, media para 8 GB** | **Diferido salvo experimento aislado** |
| **Wan2.2 TI2V-5B / LTX-2 / Mochi / HunyuanVideo nativo** | Familias abiertas o source-available de calidad contemporánea, con texto/imagen a video y, en algunos casos, audio sincronizado | Wan2.2 5B pide al menos 24 GB; LTX Desktop pide 16 GB; Mochi requiere decenas de GB; HunyuanVideo nativo también excede ampliamente 8 GB. Offload extremo no convierte estas opciones en una experiencia práctica | Proyectos oficiales y técnicamente relevantes, pero fuera del hardware objetivo. **Alta en ecosistema, incompatible localmente** | **Descartados por ahora** |

FramePack es la única primera prueba razonable porque declara explícitamente soporte para RTX 50
y 6 GB. Aun así, no debe incorporarse dentro de Fooocus ni del entorno principal: se evaluaría
como proceso supervisado independiente. El experimento usaría una imagen propia, pocos segundos,
resolución conservadora y medición de tiempo/VRAM/RAM; no se descargarían simultáneamente varias
familias de video.

Fuentes: [FramePack oficial](https://github.com/lllyasviel/FramePack),
[pesos y licencia de HunyuanVideo](https://huggingface.co/tencent/HunyuanVideo),
[LTX-Video oficial](https://github.com/Lightricks/LTX-Video),
[Wan2.1](https://github.com/Wan-Video/Wan2.1),
[Wan2.2](https://github.com/Wan-Video/Wan2.2),
[CogVideoX](https://github.com/zai-org/CogVideo) y
[optimizaciones de memoria de Diffusers](https://huggingface.co/docs/diffusers/optimization/memory).

## Embeddings

| Opción | Licencia y actividad | API y características | Recursos y compatibilidad | Privacidad, actualización y estado |
|---|---|---|---|---|
| **EmbeddingGemma 300M** | Términos de Uso de Gemma; publicado en Ollama en 2025 | `/api/embed` de Ollama; más de 100 idiomas | 622 MB, contexto 2K; puede compartir el runtime Ollama pero no debe competir con un LLM de 8B en GPU | Local; requiere Ollama 0.11.10+ y revisión de términos. **Propuesto por simplicidad, pendiente de licencia** |
| **multilingual-e5-small** | MIT; modelo ampliamente usado y mantenido en Hugging Face | Sentence Transformers; requiere prefijos coherentes de consulta/documento | ~0,1B parámetros, máximo 512 tokens; adecuado para CPU o una carga GPU breve | Local con caché controlada; registrar versión y dimensión. **Candidato liviano independiente de Ollama** |
| **BGE-M3** | MIT; proyecto activo | Dense, sparse y multi-vector; más de 100 idiomas, dimensión 1024 y hasta 8192 tokens | Más pesado que E5-small; conviene CPU o carga GPU exclusiva. Su amplitud añade complejidad innecesaria para el primer RAG | Local; buen candidato si el corpus exige documentos largos o recuperación híbrida avanzada. **Diferido** |

La primera comparación debe medir calidad en español, latencia CPU/GPU, RAM/VRAM, tamaño del
índice y estabilidad de dimensiones. No se debe cambiar de modelo de embeddings sobre un índice
existente: cada índice guardará `provider`, `model`, `revision`, `dimension`, normalización y
estrategia de chunking.

Fuentes: [EmbeddingGemma en Ollama](https://ollama.com/library/embeddinggemma),
[multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) y
[BGE-M3](https://huggingface.co/BAAI/bge-m3).

## RAG y búsqueda local

| Opción | Licencia y actividad | Beneficio | Costo y riesgo | Estado |
|---|---|---|---|---|
| **SQLite FTS5 + sqlite-vec** | SQLite public domain; sqlite-vec MIT/Apache-2.0, activo pero pre-1.0 | Usa la persistencia local existente, permite búsqueda léxica y vectorial sin servicio adicional | `sqlite-vec` puede introducir cambios incompatibles; falta diseñar chunking, filtros, migraciones y evaluación | **Base existente; propuesta para el primer prototipo RAG** |
| **LlamaIndex Core** | MIT; activo | Conectores, ingestión, retrieval, reranking e integración Ollama/Hugging Face | Superficie y dependencias mayores; puede solapar contratos propios y ocultar decisiones de persistencia | **Candidato si la ingestión supera al prototipo local** |
| **LangChain** | MIT; muy activo | Ecosistema amplio de loaders, retrievers y agentes | Ritmo de cambios y abstracciones amplias para una necesidad inicial pequeña | **Diferido** |
| **Haystack** | Apache-2.0; activo | Pipelines explícitos y RAG orientado a producción | Es otro framework completo y añade costo de adopción sin necesidad demostrada | **Diferido** |

La recomendación inicial es implementar un flujo pequeño y observable: documento → extracción →
chunks → FTS5 → embeddings opcionales → `sqlite-vec` → combinación de rankings → contexto con
referencias. No adoptar un framework hasta que un experimento demuestre una carencia concreta.
Fuentes: [FTS5](https://www.sqlite.org/fts5.html),
[sqlite-vec](https://github.com/asg017/sqlite-vec),
[LlamaIndex](https://github.com/run-llama/llama_index),
[LangChain](https://github.com/langchain-ai/langchain) y
[Haystack](https://github.com/deepset-ai/haystack).

## Monitoreo de recursos

No existe una única biblioteca Python que entregue, con igual fidelidad, telemetría de procesos y
VRAM para NVIDIA, AMD e Intel en Windows, Linux y macOS. La interfaz debe ser multiproveedor y
degradar por capacidad.

| Proveedor | Licencia y actividad | Cobertura | Limitación | Estado |
|---|---|---|---|---|
| **psutil** | BSD-3-Clause; estable y activo | CPU, RAM, disco, red y procesos en Windows/Linux/macOS/BSD | No reemplaza telemetría GPU | **Base existente; proveedor de sistema** |
| **NVML mediante nvidia-ml-py** | API NVIDIA y binding oficial mantenido | VRAM, utilización, temperatura, potencia y procesos NVIDIA; Windows/Linux | Solo NVIDIA; soporte limitado para algunas métricas GeForce | **Base existente; proveedor GPU principal** |
| **nvidia-smi** | Incluido con el driver NVIDIA | Diagnóstico y fallback por proceso/subproceso | Parsear CLI es menos estable y más costoso que NVML | **Candidato de diagnóstico, no bucle principal** |
| **PyTorch CUDA stats** | Licencia PyTorch BSD-style | Memoria asignada/reservada por el allocator del proceso propio | No observa Ollama, Fooocus ni otros procesos | **Fuente complementaria cuando el backend sea PyTorch** |
| **nvitop API** | API Apache-2.0; CLI/TUI GPL-3.0; activo y compatible con Windows/Linux | Abstracciones de dispositivo y procesos sobre NVML + psutil | Continúa siendo solo NVIDIA y añade una dependencia sobre datos ya disponibles | **Diferido; útil como herramienta externa de comparación** |
| **AMD SMI** | Stack ROCm abierto y activo | Telemetría AMD mediante biblioteca y Python | La documentación estable del componente SMI se centra en Linux; no sirve como backend Windows universal | **Candidato futuro para Linux/AMD** |

Orden de fallback propuesto: `NVML -> nvidia-smi` para NVIDIA y `psutil` siempre para sistema y
procesos. Las métricas PyTorch se agregan solo dentro de adaptadores PyTorch. Un equipo sin
proveedor GPU debe mostrar “no disponible”, no cero. Fuentes:
[NVML](https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html),
[psutil](https://github.com/giampaolo/psutil),
[nvitop](https://github.com/XuehaiPan/nvitop),
[estadísticas CUDA de PyTorch](https://docs.pytorch.org/docs/stable/cuda) y
[AMD SMI](https://rocm.docs.amd.com/projects/amdsmi).

## Experimentos propuestos, no autorizados

| Prioridad | Experimento acotado | Beneficio esperado | Costo y riesgo | Criterio de salida |
|---|---|---|---|---|
| 1 | Instalar Ollama sin pesos y comprobar versión, bind, API, directorio de modelos y configuración | Valida el runtime de la próxima vertical sin una descarga grande | Instalación de software y servicio local | Salud por loopback, sin listeners públicos ni descargas |
| 2 | Descargar Phi-4-mini Q4 (2,5 GB) y ejecutar un smoke test a 4K | Valida streaming, cancelación, residencia y liberación con bajo riesgo de OOM | Descarga de pesos y uso de disco | Carga/descarga observable; memoria vuelve al umbral esperado |
| 3 | Comparar Qwen3 8B Q4 y Qwen2.5-Coder 7B Q4 en un conjunto pequeño español/código | Selecciona modelos por tarea con mediciones locales | Aproximadamente 9,9 GB de pesos entre ambos; no cargarlos juntos | Calidad, tokens/s, primer token, VRAM pico y RAM registrados |
| 4 | Instalar faster-whisper en entorno aislado y transcribir un audio corto con `small`/int8 | Valida la suite de voz y compatibilidad CTranslate2/cuDNN | Paquetes nativos, modelo y futura instalación de FFmpeg para otros backends | Texto, timestamps, tiempo y memoria reproducibles; liberación correcta |
| 5 | Preparar Fooocus sin ejecutar descargas automáticas y verificar el stack con RTX 5060 | Resuelve temprano el mayor riesgo de Blackwell y dependencias antiguas | Repositorio/paquete grande, entorno propio y varios GB de pesos después | Preflight enumera pesos; proceso inicia en loopback y se detiene limpio |
| 6 | Comparar EmbeddingGemma y multilingual-e5-small sobre un corpus español pequeño | Decide embedding por calidad/costo antes de fijar esquema vectorial | Dos descargas y reconstrucción separada de índices | Recall cualitativo, latencia, tamaño y consumo documentados |

Cada fila requiere una aprobación explícita separada o una autorización que delimite claramente
el conjunto. Una aprobación debe indicar como mínimo herramienta/modelo, variante, origen y
descarga estimada. Instalar el runtime no autoriza a descargar modelos; aprobar un modelo no
autoriza actualizaciones futuras.

## Recomendación de incorporación

- Mantener **Ollama** como único runtime LLM de la primera vertical.
- Usar **Phi-4-mini Q4** como prueba técnica y **Qwen3 8B Q4** como primer modelo general.
- Añadir **Qwen2.5-Coder 7B Q4** solo cuando exista una tarea de código que justifique otro peso.
- Elegir **faster-whisper** como primer backend de voz; conservar OpenAI Whisper y whisper.cpp
  como referencias y fallbacks.
- Integrar **Fooocus** como proceso aislado y supervisado, con descarga automática bloqueada.
- Mantener **ComfyUI** en evaluación por su API y actividad, sin desplazar todavía el requisito
  de Fooocus.
- Empezar RAG con **FTS5 + sqlite-vec** y un embedding pequeño; no añadir un framework completo
  hasta demostrar la necesidad.
- Implementar monitoreo con proveedores: **psutil + NVML**, fallback de diagnóstico a
  `nvidia-smi` y métricas PyTorch solo para procesos propios.
- Mantener **TTS, generación de video y separación musical fuera del plan original y de los
  contratos actuales**. Si se autoriza esa extensión en el futuro, comenzar respectivamente con
  Kokoro/Chatterbox, python-audio-separator con MDX y BS-RoFormer, y FramePack como pruebas
  acotadas; Qwen3-TTS queda como comparación SOTA posterior.

## Condiciones para registrar una decisión

Cuando el usuario apruebe una incorporación se añadirá una decisión por herramienta o familia,
nombrada por responsabilidad y no por fase. Debe registrar:

- versión, variante o digest aprobado;
- fuente oficial y licencia del código y de los pesos;
- finalidad y alternativa descartada;
- compatibilidad medida en este equipo;
- rutas de pesos, caché, entradas y salidas;
- comportamiento de red y actualizaciones;
- procedimiento de carga, liberación y desinstalación;
- límites de VRAM/RAM y condiciones para revertir la decisión.
