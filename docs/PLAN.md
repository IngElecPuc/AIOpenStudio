# Plan inicial de implementación

El desarrollo será incremental: cada fase debe dejar una pieza utilizable y verificable antes de incorporar la siguiente. La selección final de variantes de Fooocus y Whisper dependerá de compatibilidad, mantenimiento, licencia y hardware.

## Fase 0 — Descubrimiento del entorno

**Objetivo:** conocer las restricciones reales del equipo y evitar decisiones de CUDA incompatibles.

**Estado:** inventario inicial completado el 16 de agosto de 2026 y actualizado el 20 de agosto.
La matriz, rutas, presupuestos, amenazas y pendientes están en `docs/environment-compatibility.md`.
Ollama y PyTorch están disponibles; FFmpeg externo no es requisito para faster-whisper/PyAV.

- Inventariar sistema operativo, Python, GPU, VRAM, RAM, almacenamiento, driver y versiones CUDA soportadas.
- Confirmar instalación y estado de Ollama.
- Definir dónde vivirán pesos, cachés, entradas y resultados.
- Establecer presupuestos de VRAM/RAM y reglas de descarga automática.
- Documentar amenazas locales: secretos, contenido privado, puertos y permisos de procesos.

**Salida:** matriz de compatibilidad y configuración local documentada.

## Fase 1 — Base del proyecto y contratos

**Objetivo:** fijar límites arquitectónicos antes de implementar integraciones.

**Estado:** completada. El paquete usa layout `src` con `pyproject.toml`; existen contratos de runtime, catálogo, proceso, residencia RAM/GPU, monitoreo y memoria conversacional. SQLite 3.49.1, FTS5 y `sqlite-vec` 0.1.9 fueron comprobados en Python 3.12. Las pruebas verifican imports sin efectos secundarios.

- Crear estructura `src`, `tests`, `docs`, `models` y `data`.
- Definir contratos de runtime, catálogo, ciclo de vida y monitoreo.
- Definir estados separados para proceso, RAM y dispositivo de cómputo.
- Incorporar configuración con Pydantic Settings.
- Configurar calidad, logging y pruebas mínimas.

**Salida:** imports válidos, contratos probados y ninguna descarga de modelos al importar módulos.

## Fase 2 — Búsqueda y sugerencia de herramientas de IA

**Objetivo:** evaluar alternativas antes de comprometer la arquitectura o descargar recursos grandes.

**Estado:** catálogo inicial completado el 17 de agosto de 2026 en
`docs/tooling-catalog.md`. La biblioteca externa compartida, su esquema, manifiesto y script de
descarga quedaron preparados sin descargar pesos. Los experimentos y decisiones por herramienta
continúan pendientes de autorización.

- Mantener un catálogo de candidatos para LLM, imagen, voz, embeddings, RAG y monitoreo.
- Evaluar cada opción por licencia, actividad, API, compatibilidad Windows/CUDA, VRAM/RAM, tamaño, privacidad y actualización.
- Comparar variantes de Whisper y el mecanismo de integración de Fooocus.
- Evaluar herramientas de monitoreo multiplataforma además de NVML.
- Proponer incorporaciones con beneficios, costos, riesgos y un experimento acotado.
- Obtener aprobación antes de clonar repositorios, instalar herramientas o descargar pesos.

**Salida:** `docs/tooling-catalog.md` y una decisión registrada por cada herramienta aprobada.

## Fase 3 — Vertical LLM con Ollama

**Objetivo:** entregar la primera suite funcional de extremo a extremo.

**Estado:** completada el 17 de agosto de 2026. El adaptador Ollama, catálogo reconciliado, ciclo de
vida, streaming, cancelación, memoria SQLite y tab LLM fueron verificados con pruebas unitarias, un
smoke test real, cancelación real y revisión visual. Los reportes confirmaron carga en GPU,
liberación completa y ausencia de descargas implícitas.

- Implementar cliente Ollama, comprobación de salud y catálogo de modelos.
- Listar, cargar, ejecutar y descargar modelos mediante el contrato común.
- Implementar chat con streaming, cancelación y manejo de errores.
- Mantener la UI independiente del SDK de Ollama.
- Probar con dobles de prueba y una integración optativa contra Ollama local.

**Salida:** tab LLM operativo con un modelo ya instalado; ninguna descarga implícita.

## Fase 4 — Monitor de recursos y políticas de residencia

**Objetivo:** hacer visible y controlable el uso intensivo de recursos.

**Estado:** completada el 17 de agosto de 2026. El panel, las políticas y las transiciones fueron
validados con 36 pruebas, Ruff, Mypy, preflight real, revisión visual y dos observaciones de hardware.
El run final tomó 30 muestras en 30,071 segundos, con intervalo medio de 1,016 segundos, cuatro
proveedores disponibles y ninguna advertencia. La prueba manual confirmó carga, inferencia y
liberación de un modelo con recuperación de RAM/VRAM.

- Mostrar GPU, VRAM, RAM, CPU y procesos asociados.
- Implementar refresco fuera del hilo de Tkinter.
- Añadir acciones explícitas para liberar un modelo y políticas de inactividad configurables.
- Diferenciar “runtime activo”, “modelo en RAM” y “modelo en GPU”.
- Degradar correctamente en equipos sin NVIDIA o sin GPU compatible.
- Mostrar tokens de la última inferencia y distinguir mediciones físicas, reportadas, derivadas,
  estimadas y desconocidas.
- Publicar una frontera de registro para telemetría futura de PyTorch/Hugging Face sin importar esos
  frameworks desde el núcleo o la UI.

**Salida:** panel de recursos y pruebas de transición de estados.

## Fase 5 — Suite Whisper

**Objetivo:** transcribir audio local con selección de modelo y dispositivo.

**Estado:** implementación completada el 20 de agosto de 2026. Existen contratos tipados, worker
aislado de faster-whisper, descubrimiento local sin descargas, progreso, cancelación fuerte,
exportadores, telemetría, micrófono en Whisper y dictado para LLM con cesión temporal de VRAM. La UI
distingue selección y residencia y reemplaza el modelo cargado sin liberación manual. Dos runs CPU
con `small` y la batería de 42 pruebas están aprobados; Ruff y Mypy están limpios. Quedan pendientes
los runs GPU, cancelación real, micrófono, cambio a `medium` y OOM.

- Implementar el adaptador elegido en la fase 2.
- Añadir tab para entrada, idioma, progreso, cancelación y exportación.
- Gestionar carga/descarga del modelo y archivos temporales.
- Probar CPU, GPU disponible y falta de memoria.

**Salida:** transcripción local reproducible y exportable.

## Fase 6 — Suite Fooocus

**Objetivo:** controlar generación de imágenes desde una suite dedicada.

**Estado:** vertical implementada y validada con GPU el 20 de agosto de 2026. Incluye
proceso aislado supervisado, transporte Gradio descubierto dinámicamente, contratos neutrales, cola
FIFO, cancelación cooperativa/fuerte, tab completo, outputs verificados por ejecución, metadatos,
telemetría y exclusión GPU con suspensión/restauración de LLM y Whisper. La aplicación no instala ni
descarga Fooocus o checkpoints. El preflight, una generación 1024×1024 y cancelaciones durante carga
y sampler están aprobados sobre Blackwell/CUDA 12.8. Quedan pendientes concurrencia con LLM y
Whisper, pasada completa de UI y OOM deliberado.

- Definir la frontera con Fooocus: API o proceso supervisado.
- Añadir tab de prompt, parámetros, cola, progreso, cancelación y galería.
- Aislar outputs y metadatos por ejecución.
- Gestionar conflictos de VRAM con otras suites.

**Salida:** generación controlada sin bloquear la aplicación.

## Fase 7 — Persistencia PostgreSQL opcional

**Objetivo:** conservar configuraciones, ejecuciones y metadatos cuando se configure una base.

**Estado:** implementación completada el 21 de agosto de 2026. La configuración permite solo
SQLite, SQLite autoritativo con réplica y PostgreSQL principal. El último escribe directamente en
PostgreSQL mientras está conectado y activa un fallback SQLite durable ante desconexión sin cambiar
la preferencia guardada. Un outbox transaccional conserva las escrituras locales pendientes. El
perfil se administra desde Tkinter, los secretos quedan fuera del repositorio y Alembic puede
autocrear/actualizar tablas dentro de una base existente. La batería segura está aprobada y el extra
`postgres` está instalado; queda pendiente ejecutar la validación real/manual con las credenciales
aportadas por el usuario, descrita en `docs/postgres-validation.md`.

- Diseñar entidades y migraciones; no almacenar binarios grandes en tablas por defecto.
- Implementar SQLAlchemy 2.x detrás de repositorios/contratos.
- Validar datos con Pydantic y gestionar credenciales por variables de entorno.
- Mantener un modo sin base de datos.

**Salida:** historial persistente opcional con migraciones y pruebas.

## Fase 8 — Robustez y distribución

**Objetivo:** preparar una aplicación mantenible para uso diario.

**Estado:** en progreso. Los seis bloques tienen código implementado: logs JSONL con sesión y
redacción, diagnóstico exportable, reconciliación de ejecuciones interrumpidas, reinicio acotado de
workers Whisper/Fooocus y cierre centralizado con persistencia al final. El contrato de distribución
Windows actualizable ya prohíbe mezclar binarios con datos y exige actualización atómica/rollback.
La matriz segura automatiza concurrencia GPU, cancelación de espera, presión sintética y cierre. La
especificación PyInstaller `onedir`, el verificador de privacidad y el ZIP/manifiesto deterministas
están listos sin instalar herramientas. La ayuda offline, guía pública, solución de problemas,
inventario estricto de licencias y reporte automatizado de candidatura completan el código previsto.
Quedan pendientes las pasadas reales, revisión humana de licencias, construir/probar el artefacto en
Windows limpio y el futuro activador de updates/rollback.

- Añadir logs estructurados, diagnósticos y recuperación ante procesos caídos.
- Probar concurrencia, cancelación, presión de memoria y cierres limpios.
- Revisar licencias de código, modelos y redistribución.
- Evaluar empaquetado para Windows y estrategia de actualizaciones.
- Crear guía de usuario y solución de problemas.

**Salida:** candidato de distribución reproducible.

## Fase 9 — Capacidades avanzadas de Fooocus

**Objetivo:** exponer de forma segura y completa las funciones de Fooocus v2.5.5 basadas en
imágenes de referencia y cerrar las validaciones reales pendientes de la suite.

**Estado:** implementación segura en progreso desde el 21 de agosto de 2026. Ya existen contratos
neutrales, staging y normalización de entradas, descubrimiento del esquema Gradio, adaptadores para
las operaciones enumeradas, cola/visor de referencias, controles Enhance/Describe y galería con
memoria optativa. Las pruebas locales no usan GPU ni descargan activos. Continúan bloqueadas hasta
autorización la adquisición de activos auxiliares, la matriz real completa, los intercambios con
LLM/Whisper y el OOM deliberado. Cada activo adicional debe inventariarse por origen, licencia,
tamaño y hash antes de incorporarse.

- Diseñar contratos neutrales que tipen imágenes fuente, máscaras, modo de transformación,
  intensidad, regiones, controles y resultados sin filtrar componentes ni tipos de Gradio hacia
  `core`, servicios o UI.
- Copiar y verificar entradas y máscaras dentro del directorio de cada ejecución, con límites de
  formato/tamaño, metadatos reproducibles y separación estricta entre archivos fuente, temporales y
  outputs finales.
- Incorporar variaciones sutiles y fuertes, upscale 1,5x/2x, inpaint, outpaint, `Image Prompt`,
  `PyraCanny`, `CPDS`, `FaceSwap`, `Describe` y `Enhance`, incluida la combinación de varias
  referencias y los parámetros que Fooocus admita para cada modo.
- Añadir una cola de imágenes de contexto con agregar, quitar, reordenar, habilitar/deshabilitar y
  previsualizar. Descubrir dinámicamente el número de ranuras de `Image Prompt` —Fooocus v2.5.5
  configura cuatro por defecto— y no confundirlas con los modos que sólo aceptan una fuente o una
  combinación fuente/máscara.
- Aceptar inicialmente `.png`, `.jpg`, `.jpeg` y `.bmp` en AIOpenStudio, validar el contenido real y
  normalizar de forma segura a RGB/RGBA antes de entregarlo a Fooocus. Someter `.webp`, `.tif`,
  `.tiff` y `.gif` a una prueba explícita contra el transporte fijado antes de habilitarlos; rechazar
  archivos animados/multipágina, imágenes desproporcionadas y contenido activo como SVG.
- Descubrir y validar el esquema Gradio real detrás del adaptador; mantener la UI independiente de
  índices, etiquetas y detalles del transporte, y fallar de forma localizada si una capacidad no
  está disponible en la versión instalada.
- Extender el tab con selección y previsualización de fuentes/máscaras, controles condicionados por
  capacidad, edición clara de outpaint y regiones, cola, progreso, cancelación, galería y mensajes
  de preflight accionables.
- Incorporar un visor que distinga referencias, máscaras y resultados, con miniaturas, vista
  ampliada y navegación anterior/siguiente por la galería de la sesión. La galería será transitoria
  por defecto; una opción explícita permitirá recordar sólo su índice y metadatos entre reinicios,
  sin duplicar binarios. «Olvidar galería» no borrará outputs: la eliminación de archivos será una
  acción separada, confirmada y auditable.
- Catalogar antes de usarlos los activos auxiliares requeridos —en particular los de inpaint— y
  bloquear cualquier descarga o actualización implícita durante preflight, arranque y generación.
- Preservar la exclusión GPU, la suspensión/restauración de LLM y Whisper, el proceso supervisado,
  la cola FIFO, la cancelación fuerte, la recuperación ante caída/OOM y la telemetría por ejecución.
- Añadir pruebas unitarias y de integración optativa para contratos, validación de archivos,
  argumentos descubiertos, aislamiento de artefactos, capacidades ausentes, cancelación y errores;
  mantener las pruebas de hardware explícitas y omitibles.
- Completar una matriz manual con al menos un run real por capacidad y verificar calidad funcional,
  metadatos, progreso, cancelación, recuperación de recursos y ausencia de descargas inesperadas.
- Validar por separado una y varias referencias, formatos permitidos/rechazados, cambio de orden y
  habilitación, reapertura o no de la galería según su política y comportamiento con entradas
  borradas o modificadas desde fuera de la aplicación.
- Cerrar además los pendientes de la fase 6: intercambio real con un LLM residente y activo,
  intercambio real con Whisper, recorrido completo del tab —incluidas dos tareas FIFO y
  cancelaciones activa/en cola— y recuperación después de un OOM deliberado autorizado.

**Salida:** suite Fooocus con todas las capacidades avanzadas enumeradas disponibles mediante
contratos desacoplados y UI propia, con matriz de validación segura/real documentada y recuperación
comprobada ante cancelación, conflicto de GPU, fallo del proceso y OOM.

**Fuentes de diseño:** [Fooocus v2.5.5: capacidades y parámetros](https://github.com/lllyasviel/Fooocus/blob/v2.5.5/modules/flags.py),
[UI y ranuras de imágenes de referencia](https://github.com/lllyasviel/Fooocus/blob/v2.5.5/webui.py)
y [configuración de Fooocus](https://github.com/lllyasviel/Fooocus/blob/v2.5.5/modules/config.py).

## Fase 10 — Conversaciones, contexto y controles LLM

**Objetivo:** convertir la suite LLM en un espacio conversacional persistente, multimodal y
configurable, sin acoplarla a Ollama ni enviar contexto externo sin una decisión visible.

**Estado:** propuesta. Se aprovecharán primero las capacidades declaradas por los tags ya
instalados; esta fase no autoriza descargas ni presume que todos los modelos admitan visión,
thinking, herramientas o los mismos parámetros.

- Incorporar un navegador de conversaciones con título visible, búsqueda y acciones para crear,
  abrir, continuar, renombrar, archivar, exportar y eliminar con confirmación. El título podrá
  derivarse localmente del primer mensaje y siempre será editable; se mostrará inequívocamente la
  conversación activa y se advertirá al cambiar de modelo dentro de ella.
- Garantizar reapertura y continuidad entre reinicios sobre el repositorio de conversaciones. El
  contenido seguirá siendo local por defecto; cualquier réplica PostgreSQL de mensajes o adjuntos
  requerirá una política de privacidad independiente y explícita.
- Crear una cola de contexto externo con agregar, quitar, reordenar, previsualizar y un checkbox por
  elemento. Cada entrada tendrá política de envío `una vez` o `en cada turno`; la cola y sus checks
  serán efímeros por defecto y sólo se recordarán por conversación mediante una opción explícita.
- Admitir como texto `.txt`, `.json`, `.yaml`, `.yml`, `.md`, `.py`, `.c`, `.cpp`, `.h`, `.hpp`,
  `.js`, `.ts`, `.tsx`, `.html`, `.css` y `.sql`. Leerlos como UTF-8/UTF-8 BOM, detectar binarios,
  limitar tamaño individual y total, no ejecutar su contenido y delimitarlo como datos externos
  potencialmente no confiables para reducir inyección de instrucciones.
- Registrar tamaño, hash y fecha de modificación de los adjuntos; avisar si el archivo cambió o ya
  no existe. Persistir sólo referencias por defecto y ofrecer una copia/snapshot reproducible
  únicamente con consentimiento, sin incorporar archivos del usuario al repositorio del proyecto.
- Añadir imágenes de contexto con miniatura cuando el tag anuncie `vision` mediante `/api/show`.
  Validarlas y normalizarlas localmente, permitir varias sólo si la capacidad real lo admite y no
  ofrecer visión por nombre de familia: Gemma 3 tiene variantes sólo-texto y multimodales, mientras
  que el tag concreto de Gemma 4 también debe comprobarse.
- Exponer ajustes comunes con restauración a los valores del modelo: temperatura, `top_p`, `top_k`,
  `min_p`, semilla, ventana de contexto, máximo de tokens nuevos, penalización de repetición,
  secuencias de parada y prompt de sistema. Separar controles comunes de ajustes avanzados propios
  del backend y advertir el impacto de `num_ctx` sobre RAM/VRAM.
- Modelar thinking como capacidad: no disponible, booleano o niveles. Ofrecer respuesta final
  directa mediante `think=false` cuando el tag realmente lo soporte y, por separado, ocultar o
  mostrar la traza cuando no pueda desactivarse. Separar `thinking` de `content` durante streaming y
  no persistir ni reinyectar trazas de razonamiento por defecto.
- Incorporar presupuestos y conteo de tokens, truncamiento controlado y compactación mediante
  resúmenes versionados antes de exceder la ventana. Conservar el historial íntegro y permitir
  inspeccionar, regenerar o descartar el resumen; nunca perder instrucciones del sistema,
  decisiones confirmadas, restricciones ni hechos pendientes de validación.
- Definir una política visible para respuestas parciales canceladas y cambios de modelo. Evaluar
  reutilización de prefijos o caché KV sólo en runtimes que la declaren, con aislamiento entre
  conversaciones, invalidación y métricas de latencia, tokens y memoria.
- Renderizar Markdown de forma segura con alternativa de texto plano, enlaces controlados, bloques
  de código copiables y buen rendimiento durante streaming. Ofrecer salida estructurada JSON/JSON
  Schema sólo cuando el runtime lo admita y validarla con Pydantic.
- Mantener herramientas deshabilitadas por defecto. Si se incorporan, usar contratos neutrales,
  lista permitida y confirmación de acciones; la búsqueda web de Ollama Cloud queda fuera del
  alcance local inicial por requerir red, cuenta y credenciales.
- Probar persistencia y borrado, adjuntos modificados/ausentes, límites y prompt injection,
  presupuestos largos, cancelación, cambio de modelo y concurrencia. Ejecutar una matriz real con
  al menos un tag sólo-texto, uno con visión y uno con thinking, sin descargar modelos para la
  prueba, y documentar capacidades solicitadas frente a las aplicadas.

**Salida:** suite LLM con navegador de conversaciones, contexto externo seleccionable,
multimodalidad condicionada por capacidades, controles de generación y razonamiento verificables,
presupuestos de contexto y continuidad local entre reinicios.

**Fuentes de diseño:** [API de chat de Ollama](https://docs.ollama.com/api/chat),
[thinking](https://docs.ollama.com/capabilities/thinking),
[visión](https://docs.ollama.com/capabilities/vision),
[inspección de capacidades del modelo](https://docs.ollama.com/api-reference/show-model-details),
[parámetros de Modelfile](https://docs.ollama.com/modelfile),
[Gemma 3](https://ollama.com/library/gemma3) y [Gemma 4](https://ollama.com/library/gemma4).

## Fase 11 — Transcripción y traducción avanzadas con Whisper

**Objetivo:** exponer las capacidades útiles de faster-whisper/Whisper que aún no están disponibles
en la suite, con controles comprensibles, exportación reproducible y límites claros del backend.

**Estado:** propuesta. La versión fijada es faster-whisper 1.2.1. No se instalarán modelos ni
dependencias adicionales; cada control se descubrirá o adaptará tras el contrato común.

- Añadir detección automática de idioma con probabilidad visible y selección manual. Permitir
  `transcribe` y traducción nativa a inglés, avisando que Whisper no traduce directamente a otros
  idiomas y que `turbo` no es el modelo recomendado para traducción.
- Exponer timestamps por palabra y puntuación/confianza cuando estén disponibles, además de la
  segmentación actual. Añadir tabla navegable por segmentos/palabras, búsqueda y corrección no
  destructiva antes de exportar.
- Incorporar VAD Silero con modo desactivado, automático y presets comprensibles, más controles
  avanzados para silencio cuando proceda. Mostrar cuánto audio fue descartado y conservar los
  parámetros exactos junto al resultado.
- Añadir prompt inicial, prefijo y glosario/hotwords para nombres propios o vocabulario técnico,
  diferenciando qué opciones son incompatibles con la inferencia por lotes fijada.
- Exponer en un panel avanzado `beam_size`, `best_of`, `patience`, temperaturas de fallback,
  umbrales de no-habla/logprob/compresión, condicionamiento con texto previo, puntuación incluida y
  detección de silencios alucinados, con presets seguros y restauración a valores del backend.
- Permitir seleccionar uno o varios intervalos de tiempo y procesar una cola de audios. Usar
  ejecución secuencial por defecto; habilitar `BatchedInferencePipeline` sólo después de medir RAM,
  VRAM, cancelación y diferencias de resultado frente al pipeline actual.
- Ampliar exportación con TSV/CSV y JSON detallado que conserve idioma/probabilidad, modelo,
  dispositivo, opciones, segmentos, palabras y métricas. Regenerar TXT/SRT/VTT desde las
  correcciones sin perder el resultado original.
- Evaluar una vista de dictado por fragmentos con solapamiento y deduplicación como función
  experimental; no presentarla como streaming nativo ni prometer latencia estable, porque
  Whisper/faster-whisper procesan ventanas de audio.
- Mantener diarización e identificación de hablantes fuera del alcance nativo: sólo podrá abrirse
  como integración futura con otro backend, licencia, modelos y presupuesto de memoria revisados y
  autorizados por separado.
- Cerrar los pendientes reales de la fase 5 —GPU, micrófono, cancelación, cambio a `medium` y OOM
  autorizado— y añadir pruebas de traducción español→inglés, timestamps por palabra, VAD,
  hotwords, intervalos, audio largo, cola/batch, exportaciones y recuperación tras fallo.

**Salida:** suite Whisper con idioma y traducción controlables, detalle por palabra, VAD, glosarios,
decodificación avanzada, cola/intervalos y exportaciones editables, con límites de streaming y
diarización documentados y matriz real de validación.

**Fuentes de diseño:** [Whisper oficial](https://github.com/openai/whisper/blob/main/README.md),
[faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/README.md) y
[parámetros de transcripción fijados](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py).

## Criterios transversales

- Ninguna operación costosa bloquea el hilo de Tkinter.
- Ningún backend puede derribar suites no relacionadas.
- Descargar, cargar y liberar recursos siempre requiere una acción o política visible.
- Los contratos no exponen tipos de SDKs externos.
- Los tests con GPU, Ollama, Fooocus, Whisper o PostgreSQL son explícitos y se pueden omitir.
- No se crea ningún commit sin autorización del usuario.
