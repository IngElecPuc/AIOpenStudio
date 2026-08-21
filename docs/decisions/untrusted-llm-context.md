# Contexto LLM externo como dato no confiable

- **Estado:** aceptada para la implementación local.
- **Fecha:** 21 de agosto de 2026.

## Decisión

Los archivos elegidos como contexto se tratan como datos externos potencialmente no confiables. Se
validan sin ejecutar, importar ni interpretar código. El texto se limita a extensiones aprobadas,
UTF-8/UTF-8 BOM y límites configurables; las imágenes se verifican por contenido y se normalizan en
una caché privada antes de entregarlas al runtime.

Agregar un elemento no autoriza su envío: nace deshabilitado y cada turno sólo incluye los checks
activos. La cola es efímera por defecto. Recordarla persiste referencias y metadatos en SQLite, no
bytes ni contenido en PostgreSQL. Una copia reproducible requiere `snapshot=True` explícito y se
guarda bajo datos locales ignorados por Git.

Cada texto se delimita con un marcador único y el sistema indica que no se obedezcan instrucciones
contenidas en él. Este control mitiga prompt injection, pero no se presenta como aislamiento fuerte.
Las herramientas siguen deshabilitadas.

La visión se habilita exclusivamente desde capacidades del tag exacto. `/api/show` no declara un
máximo general de imágenes, por lo que el adaptador conserva una sola hasta que una validación por
digest demuestre más.

## Presupuesto

El preflight usa una estimación transparente y reserva salida antes de inferir. El conteo real llega
después mediante `prompt_eval_count`. El desborde se rechaza por defecto; truncar lo más antiguo es
una política explícita que afecta sólo el request ensamblado y nunca elimina mensajes almacenados.
Los resúmenes son versionados, conservan rangos y hechos protegidos y no reemplazan el historial
íntegro.
