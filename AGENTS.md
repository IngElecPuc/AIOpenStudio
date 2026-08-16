# AGENTS.md

## Propósito

AIOpenStudio es una aplicación de escritorio local, orientada a objetos, para operar modelos de IA mediante suites desacopladas. Las suites iniciales son LLM, Fooocus y Whisper; Ollama es el primer runtime de LLM.

## Reglas de trabajo

- No crear commits, ramas, tags, pushes ni pull requests sin permiso explícito del usuario.
- Usar exclusivamente estas convenciones de commit:
  - `feat (scope): description`
  - `fix (scope): description`
  - `refactor (scope): description`
  - `docs (scope): description`
- Preservar cambios ajenos y revisar `git status` antes y después de editar.
- Favorecer cambios pequeños, verificables y compatibles con Windows.
- No descargar pesos, modelos ni repositorios externos sin autorización.
- Nunca versionar secretos, credenciales, bases de datos locales, audios, imágenes generadas, pesos o cachés.

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
- Registrar decisiones relevantes en `docs/decisions/`.
- Mantener `docs/PLAN.md` con fases y criterios de salida.
- Documentar requisitos de hardware y licencias antes de incorporar un modelo o herramienta.
