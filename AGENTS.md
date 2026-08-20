# AGENTS.md

## Propósito

AIOpenStudio es una aplicación de escritorio local, orientada a objetos, para operar modelos de IA mediante suites desacopladas. Las suites iniciales son LLM, Fooocus y Whisper; Ollama es el primer runtime de LLM.

## Reglas de trabajo

- Leer `docs/environment-compatibility.md` antes de cambiar Python, PyTorch, CUDA, rutas de datos, límites de memoria o configuración de servicios locales.
- No crear commits, ramas, tags, pushes ni pull requests sin permiso explícito del usuario.
- Usar exclusivamente estas convenciones de commit:
  - `feat (scope): description`
  - `fix (scope): description`
  - `refactor (scope): description`
  - `docs (scope): description`
- Nombrar archivos y carpetas por su responsabilidad o contenido. No usar marcadores de secuencia como `phase_x`, `fase_x`, `stage_x`, `etapa_x`, `block_x`, `bloque_x`, `step_x` o equivalentes.
- Las fases y etapas pueden aparecer como estructura interna de `docs/PLAN.md`, pero no deben trasladarse a nombres de artefactos.
- Evitar fases, etapas o bloques en los mensajes de commit; describir el resultado funcional. Solo usarlos si el usuario lo autoriza explícitamente.
- Preservar cambios ajenos y revisar `git status` antes y después de editar.
- Favorecer cambios pequeños, verificables y compatibles con Windows.
- No descargar pesos, modelos ni repositorios externos sin autorización.
- Nunca versionar secretos, credenciales, bases de datos locales, audios, imágenes generadas, pesos o cachés.

## Gestión del contexto y continuidad

- Tratar el repositorio como fuente de verdad entre chats. Exteriorizar decisiones, restricciones,
  resultados de validación y deuda técnica en `README.md`, `docs/PLAN.md`, `docs/decisions/` o la
  documentación específica correspondiente.
- No depender únicamente de la memoria conversacional ni de un resumen compactado. Antes de actuar,
  releer los archivos relevantes, `AGENTS.md`, el estado de Git y las salidas persistidas que
  fundamenten la tarea.
- Mantener actualizaciones y respuestas concisas, evitando repetir antecedentes que ya estén
  documentados. Al consultar herramientas, preferir búsquedas dirigidas y resultados acotados en vez
  de volcar archivos o logs completos sin necesidad.
- Al cerrar una unidad funcional, registrar criterios de salida, verificaciones ejecutadas,
  resultados reales y trabajo pendiente antes de pasar a la siguiente.
- Cuando una conversación cambie de fase, acumule muchos artefactos o requiera volver reiteradamente
  a antecedentes antiguos, recomendar iniciar un chat nuevo aunque todavía exista capacidad de
  contexto disponible.
- Para continuar en otro chat, preparar un traspaso compacto que identifique objetivo actual, último
  commit relevante, estado sucio del repositorio, archivos fuente de verdad, decisiones vigentes,
  verificaciones aprobadas, acciones prohibidas y próximo paso concreto.
- En un chat nuevo, verificar el traspaso contra el repositorio antes de continuar. Un resumen ayuda
  a orientarse, pero no reemplaza la inspección del código, la documentación y el estado local.
- No afirmar cifras exactas sobre contexto activo, compactación, consumo o coste sin telemetría.
  Diferenciar siempre historial bruto, contexto activo estimado y procesamiento acumulado.

## Arquitectura

- Mantener la dirección de dependencias: `ui -> services -> core`.
- `core` no puede importar Tkinter, SQLAlchemy ni SDKs de runtimes.
- Las integraciones concretas pertenecen a `infrastructure` e implementan protocolos de `core.contracts`.
- La UI no invoca SDKs ni procesos externos directamente; usa servicios/casos de uso.
- Ollama pertenece a `infrastructure/runtimes/ollama`, no a una suite visual propia.
- Cada suite debe poder iniciarse en forma aislada y reportar capacidades no disponibles sin bloquear toda la app.
- PostgreSQL es opcional; el arranque básico debe funcionar sin conexión de base de datos.
- Operaciones largas o de I/O no deben ejecutarse en el hilo principal de Tkinter.

## Python

- Soportar Python 3.11 y 3.12 mientras no exista una decisión posterior.
- Usar anotaciones de tipos, `pathlib`, Pydantic para límites/configuración y SQLAlchemy 2.x para persistencia.
- Preferir composición e inyección de dependencias sobre singletons o imports globales con efectos secundarios.
- Representar backends mediante contratos; no usar condicionales por backend repartidos por la aplicación.
- Tratar carga en RAM, carga en GPU y proceso en ejecución como estados relacionados pero distintos.
- Añadir pruebas para lógica de estados, selección de dispositivo y manejo de backends no disponibles.

## Verificación

Antes de entregar cambios de código, ejecutar cuando corresponda:

```powershell
pytest
ruff check .
mypy src
```

Si una comprobación no puede ejecutarse por dependencias o hardware ausente, documentarlo claramente sin simular éxito.

## Documentación

- Actualizar `README.md` cuando cambien instalación, ejecución o estructura pública.
- Mantener `docs/environment-compatibility.md` como fuente compartida del inventario local, decisiones CUDA, rutas, presupuestos y amenazas; no registrar secretos ni identificadores innecesarios del equipo.
- Registrar decisiones relevantes en `docs/decisions/`.
- Mantener `docs/PLAN.md` con fases y criterios de salida.
- Documentar requisitos de hardware y licencias antes de incorporar un modelo o herramienta.
