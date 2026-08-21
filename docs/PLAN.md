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

**Estado:** propuesta. La integración actual continúa limitada a texto-a-imagen; esta fase no
autoriza instalaciones ni descargas. Cada activo adicional debe inventariarse por origen, licencia,
tamaño y hash, y requiere aprobación explícita antes de incorporarse.

- Diseñar contratos neutrales que tipen imágenes fuente, máscaras, modo de transformación,
  intensidad, regiones, controles y resultados sin filtrar componentes ni tipos de Gradio hacia
  `core`, servicios o UI.
- Copiar y verificar entradas y máscaras dentro del directorio de cada ejecución, con límites de
  formato/tamaño, metadatos reproducibles y separación estricta entre archivos fuente, temporales y
  outputs finales.
- Incorporar variaciones sutiles y fuertes, upscale 1,5x/2x, inpaint, outpaint, `Image Prompt`,
  `PyraCanny`, `CPDS`, `FaceSwap`, `Describe` y `Enhance`, incluida la combinación de varias
  referencias y los parámetros que Fooocus admita para cada modo.
- Descubrir y validar el esquema Gradio real detrás del adaptador; mantener la UI independiente de
  índices, etiquetas y detalles del transporte, y fallar de forma localizada si una capacidad no
  está disponible en la versión instalada.
- Extender el tab con selección y previsualización de fuentes/máscaras, controles condicionados por
  capacidad, edición clara de outpaint y regiones, cola, progreso, cancelación, galería y mensajes
  de preflight accionables.
- Catalogar antes de usarlos los activos auxiliares requeridos —en particular los de inpaint— y
  bloquear cualquier descarga o actualización implícita durante preflight, arranque y generación.
- Preservar la exclusión GPU, la suspensión/restauración de LLM y Whisper, el proceso supervisado,
  la cola FIFO, la cancelación fuerte, la recuperación ante caída/OOM y la telemetría por ejecución.
- Añadir pruebas unitarias y de integración optativa para contratos, validación de archivos,
  argumentos descubiertos, aislamiento de artefactos, capacidades ausentes, cancelación y errores;
  mantener las pruebas de hardware explícitas y omitibles.
- Completar una matriz manual con al menos un run real por capacidad y verificar calidad funcional,
  metadatos, progreso, cancelación, recuperación de recursos y ausencia de descargas inesperadas.
- Cerrar además los pendientes de la fase 6: intercambio real con un LLM residente y activo,
  intercambio real con Whisper, recorrido completo del tab —incluidas dos tareas FIFO y
  cancelaciones activa/en cola— y recuperación después de un OOM deliberado autorizado.

**Salida:** suite Fooocus con todas las capacidades avanzadas enumeradas disponibles mediante
contratos desacoplados y UI propia, con matriz de validación segura/real documentada y recuperación
comprobada ante cancelación, conflicto de GPU, fallo del proceso y OOM.

## Criterios transversales

- Ninguna operación costosa bloquea el hilo de Tkinter.
- Ningún backend puede derribar suites no relacionadas.
- Descargar, cargar y liberar recursos siempre requiere una acción o política visible.
- Los contratos no exponen tipos de SDKs externos.
- Los tests con GPU, Ollama, Fooocus, Whisper o PostgreSQL son explícitos y se pueden omitir.
- No se crea ningún commit sin autorización del usuario.

## Deuda técnica registrada

### Control de razonamiento de los LLM

- Incorporar al catálogo de runtime una capacidad de thinking por modelo y tag que distinga entre
  no compatible, control booleano (`false/true`) y niveles graduados (`low`, `medium`, `high`,
  `max`), sin ofrecer valores que el backend reduzca internamente a un simple booleano.
- Consultar las capacidades reportadas por la versión local de Ollama y mantener el catálogo
  documentado como fallback; validar cada tag mediante una inferencia acotada, sin descargas
  implícitas.
- Extender el contrato común, los servicios y la UI para seleccionar el máximo nivel admitido,
  transmitir `think` a Ollama y separar el contenido de razonamiento de la respuesta final durante
  el streaming. No persistir trazas privadas de razonamiento por defecto.
- Registrar junto a cada ejecución el ajuste solicitado, el aplicado por el runtime, tokens,
  latencia y consumo de memoria para poder comparar el costo real de cada nivel.

### Gestión conversacional de la suite LLM

- Incorporar un navegador de conversaciones persistidas con acciones para abrir, continuar,
  renombrar, archivar y eliminar de forma explícita.
- Implementar borrado seguro de conversaciones, con confirmación, eliminación en cascada de
  mensajes, resúmenes e índices de búsqueda, y una opción clara para cancelar la acción.
- Permitir editar el título. Actualmente se crea como `Nueva conversación` y se reemplaza
  automáticamente por los primeros 60 caracteres de la primera pregunta.
- Exponer en la UI los parámetros que ya admite el contrato: temperatura, `top_p`, `top_k`, semilla,
  longitud de contexto, máximo de tokens nuevos y secuencias de parada.
- Mostrar claramente qué conversación está activa y advertir cuando se cambia de modelo dentro de
  ella, porque el nuevo modelo recibe el mismo historial.
- Implementar presupuestos de contexto, conteo de tokens, truncamiento controlado y compresión con
  resúmenes antes de alcanzar la ventana máxima del modelo.
- Definir una política visible para respuestas parciales canceladas: conservarlas en el contexto,
  excluirlas del siguiente turno o permitir que el usuario decida.
- Añadir pruebas de reapertura y continuidad entre reinicios, cambio de modelo, historiales extensos
  y concurrencia entre lectura externa y escritura desde AIOpenStudio.

### Configuración y presentación de la interfaz

- Añadir un menú desplegable superior que permita acceder a parámetros y controles de cada suite y
  del modelo activo sin acoplar la UI a un runtime específico.
- Separar ajustes comunes de generación, ajustes propios del backend y configuración general de la
  suite, mostrando sólo controles compatibles con sus capacidades declaradas.
- Incorporar interpretación y renderizado seguro de Markdown en las respuestas: encabezados,
  énfasis, listas, citas, enlaces y bloques de código, conservando una alternativa de texto plano.
- Definir reglas para enlaces, contenido potencialmente peligroso, copiado de código, selección de
  texto y rendimiento con respuestas extensas o recibidas por streaming.

### Eficiencia del contexto LLM

- Evaluar e implementar prompt caching o reutilización de prefijos y caché KV en los runtimes que
  lo soporten, sin asumir que todos los backends exponen las mismas capacidades.
- Definir claves, invalidación, aislamiento entre conversaciones, límites de memoria y tratamiento
  de contenido privado para evitar reutilizar contexto incorrecto o filtrarlo entre sesiones.
- Incorporar métricas de aciertos de caché, tokens reutilizados, latencia al primer token, memoria
  consumida y ahorro efectivo antes de habilitar políticas automáticas.
- Implementar compactación conversacional basada en presupuestos de tokens, con resúmenes
  versionados y trazabilidad hacia los mensajes originales; el historial íntegro debe conservarse.
- Establecer qué información nunca puede perderse al compactar: instrucciones del sistema,
  decisiones confirmadas, restricciones, herramientas utilizadas y hechos pendientes de validar.
- Permitir regenerar, inspeccionar y descartar una compactación, además de elegir entre historial
  completo, resumen más mensajes recientes o una conversación nueva.
- Añadir evaluaciones de fidelidad y regresión para comprobar que caching y compactación mejoran
  coste y velocidad sin degradar respuestas, continuidad, privacidad ni comportamiento del modelo.
