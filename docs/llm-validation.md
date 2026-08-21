# Validación de conversaciones y capacidades LLM

Actualizado el 21 de agosto de 2026.

## Alcance implementado

- Contratos neutrales para capacidades de chat, thinking, visión, herramientas, valores comunes y
  límite de contexto.
- `min_p`, penalización de repetición y prompt de sistema en la frontera común.
- Inspección por tag y digest mediante `/api/show`, con caché local de la respuesta.
- Fallo localizado: si la inspección de un tag falla, el catálogo sigue disponible y registra el
  error sin descargar ni modificar modelos.
- Esquema SQLite v3 con migración aditiva desde v2.
- Archivo, restauración, búsqueda FTS5, renombrado, borrado en cascada y reapertura de la
  conversación activa más reciente.
- Estados explícitos de mensajes y exclusión de parciales cancelados del historial posterior.
- Metadatos versionables para resúmenes y referencias de contexto; la ingestión de archivos aún no
  estaba pendiente en la primera entrega.
- Ingestión de texto UTF-8/BOM con detección de binarios, límites individual/total, preview, hash,
  tamaño y fecha.
- Cola efímera o recordada, reordenamiento, checkbox, políticas una vez/cada turno y snapshots
  consentidos.
- PNG/JPEG/BMP verificados y normalizados; visión y cantidad de imágenes condicionadas por el tag.
- Ensamblado con datos no confiables, presupuesto conservador, truncamiento explícito y resúmenes
  versionados con hechos protegidos.
- Navegador visual con búsqueda, creación, renombrado, archivo/restauración, exportación Markdown o
  JSON y borrado confirmado que conserva los originales.
- Cola visual reordenable con checkbox, política una vez/cada turno, preview textual o miniatura y
  aceptación explícita de archivos modificados.
- Controles comunes donde vacío restaura el valor del modelo, seed aleatoria, advertencia de
  `num_ctx`, thinking por capacidad y separación de la traza durante streaming.
- Transcript Markdown nativo sin HTML ni enlaces activos, texto plano y copia de bloques de código.
- JSON/JSON Schema enviado mediante `format`, parseado con Pydantic y validado contra un subconjunto
  documentado antes de emitir el terminal completado.
- Preflight visible, streaming agrupado, respuestas canceladas/fallidas fuera del historial futuro
  y editor de resúmenes versionados.

## Verificación segura

```powershell
python -m pytest tests/unit/test_sqlite_store.py tests/unit/test_ollama_runtime.py tests/unit/test_llm_service.py -q
python -m pytest tests/unit/test_llm_context.py tests/unit/test_llm_prompt.py -q
ruff check src tests
mypy src
```

No se consultó el servidor Ollama real, no se generaron tokens y no se descargó ningún activo.
Batería completa: `129 passed, 4 skipped`; las omisiones son la nueva matriz LLM real, las
integraciones optativas con Ollama/PostgreSQL y `sqlite-vec` no instalado. Ruff y mypy aprueban la
implementación. El punto de composición se valida por separado sin iniciar procesos ni la interfaz.

## Matriz real pendiente

`tests/integration/test_llm_capability_matrix.py` exige opt-in, tres tags exactos ya instalados y una
imagen elegida por el usuario. Comprueba texto, visión y `think=false`; falla si falta un tag en vez
de descargarlo. Las variables y el comando están en `docs/llm-user-guide.md`.

Queda pendiente ejecutar y documentar esa matriz con autorización. La reutilización explícita de
caché KV se mantiene fuera del request mientras el runtime no publique un contrato verificable de
aislamiento e invalidación por conversación.
