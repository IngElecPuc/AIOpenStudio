# Catálogo inicial de herramientas y modelos de IA

Inventario de candidatos revisado el **17 de agosto de 2026**. Este documento es una
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

| Modelo y variante | Uso principal | Licencia | Archivo y contexto publicado | Encaje estimado en 8 GB | Actividad y estado |
|---|---|---|---|---|---|
| **Qwen3 8B Q4_K_M** | General, español, herramientas y razonamiento con modo thinking | Apache-2.0 | 5,2 GB; 40K | Viable con 4K, una petición y sin otra carga GPU; es el límite alto cómodo | Publicado hace alrededor de un año. **Propuesto como LLM general inicial** |
| **Qwen2.5-Coder 7B Instruct Q4_K_M** | Código, explicación, corrección y FIM | Apache-2.0 | 4,7 GB; 32K. Q5_K_M: 5,4 GB | Q4 viable; Q5 solo como comparación posterior de 2K/4K | Familia estable de hace alrededor de un año. **Propuesto para código** |
| **DeepSeek-R1 Distill Qwen 7B Q4_K_M** | Razonamiento y comparación con Qwen general | MIT sobre base Qwen Apache-2.0 | 4,7 GB; 128K | Viable con contexto restringido; las respuestas de razonamiento pueden elevar latencia y tokens | Familia estable de hace alrededor de un año. **Candidato de benchmark** |
| **Granite 4 7B-A1B-H Q4_K_M** | Herramientas, RAG, diálogo multilingüe y código | Apache-2.0 | 4,2 GB; arquitectura híbrida con contexto publicado de 1M | Buen margen, pero el backend híbrido debe probarse; nunca usar 1M en este equipo | Lanzado en 2025 y publicado en Ollama hace menos de un año. **Candidato eficiente** |
| **Phi-4-mini 3.8B Q4_K_M** | Control liviano, herramientas y tareas cortas | MIT | 2,5 GB; 128K | Amplio margen; útil para validar integración antes de un 7B/8B | Familia estable de hace alrededor de un año. **Propuesto como smoke test** |
| **Gemma 3 4B Q4_K_M** | Texto e imagen en un modelo compacto | Términos de Uso de Gemma, con obligaciones y restricciones propias | 3,3 GB; 128K; entrada de texto e imagen | Buen margen; requiere revisión de licencia antes de aprobar | Familia estable de hace alrededor de un año. **Candidato multimodal** |
| **Llama 3.1 8B Instruct Q4_K_M** | Línea base general ampliamente soportada | Meta Llama 3.1 Community License | 4,9 GB; 128K | Viable con contexto corto | Madura, pero la licencia es menos simple que Apache/MIT. **Candidato secundario** |
| **Mistral 7B Instruct v0.3 Q4_K_M** | Línea base general y function calling | Apache-2.0 | 4,4 GB; 32K | Viable y conservador | Modelo maduro, menos reciente. **Candidato de regresión** |
| **Qwen3.5 9B Q4_K_M** | Modelo general/multimodal más reciente | Apache-2.0 | 6,6 GB; 256K | Demasiado ajustado tras reservar VRAM para KV y temporales | Activo y reciente. **Diferido por memoria** |

Fuentes de tamaños y metadatos: [Qwen3](https://ollama.com/library/qwen3/tags),
[Qwen2.5-Coder](https://ollama.com/library/qwen2.5-coder/tags),
[DeepSeek-R1](https://ollama.com/library/deepseek-r1/tags),
[Granite 4](https://ollama.com/library/granite4),
[Phi-4-mini](https://ollama.com/library/phi4-mini/tags),
[Gemma 3](https://ollama.com/library/gemma3/tags),
[Llama 3.1](https://ollama.com/library/llama3.1/tags),
[Mistral 7B](https://ollama.com/library/mistral/tags) y
[Qwen3.5 9B](https://ollama.com/library/qwen3.5%3A9b).
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
5. Solo si Q4 deja margen medido: Qwen2.5-Coder Q5 a 2K/4K para cuantificar si la mejora
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

## Imagen y mecanismo de integración de Fooocus

| Opción | Licencia y actividad | API e integración | Windows/CUDA y recursos | Privacidad, actualización y estado |
|---|---|---|---|---|
| **Fooocus 2.5.5** | GPL-3.0; soporte LTS limitado a correcciones, última release oficial en 2024; basado exclusivamente en SDXL | UI Gradio. La integración programática usa controles Gradio/WebSockets, no una REST estable; cambios de UI pueden romperla | Oficialmente admite Windows/NVIDIA desde 4 GB de VRAM y 8 GB de RAM con swap. Su stack PyTorch antiguo debe probarse con RTX 5060/Blackwell | Offline tras las descargas. El primer arranque descarga modelos automáticamente y el inpaint agrega 1,28 GB: debe bloquearse esa conducta hasta aprobación. **Propuesto por requisito del proyecto, con riesgo alto de integración** |
| **ComfyUI** | GPL-3.0; proyecto muy activo, releases en 2026 | Backend/API y cola asíncrona orientados a workflows | Windows y varios proveedores GPU; gestión dinámica de VRAM y opciones de reserva/offload | Puede operar local; los nodos de API externos se pueden desactivar. **Candidato futuro y alternativa técnica a Fooocus** |

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
[discusión oficial sobre la API](https://github.com/lllyasviel/Fooocus/discussions/2772) y
[repositorio oficial de ComfyUI](https://github.com/Comfy-Org/ComfyUI).

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
