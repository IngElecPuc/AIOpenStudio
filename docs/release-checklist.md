# Checklist de candidato Windows

Este documento separa barreras automatizadas de comprobaciones manuales. Un ZIP generado no se
considera candidato aprobado hasta completar ambas secciones en un Windows limpio.

## Barreras automatizadas

- [ ] Ruff, Mypy y batería segura completos.
- [ ] Bundle `onedir` generado desde un commit identificable.
- [ ] Verificador sin rutas privadas, secretos, bases, datos o configuración local.
- [ ] `LICENSE`, `THIRD_PARTY_NOTICES.txt` e inventario JSON presentes.
- [ ] Ninguna dependencia tiene licencia `UNKNOWN`.
- [ ] Guía y solución de problemas incluidas.
- [ ] ZIP y manifiesto coinciden en nombre, tamaño, versión y SHA-256.
- [ ] Estructura ZIP sin rutas absolutas o escapes `..`.
- [ ] Reporte de candidatura con estado `passed`.

## Validación manual en Windows limpio

- [ ] Inicia sin Python, Git, repositorio, `.env`, PostgreSQL o modelos instalados.
- [ ] Muestra capacidades ausentes sin bloquear las suites restantes.
- [ ] Crea datos únicamente en directorios de usuario.
- [ ] Configura SQLite y reinicia conservando preferencias.
- [ ] Conecta PostgreSQL opcional, reinicia y prueba fallback advertido.
- [ ] Ejecuta LLM, Whisper y Fooocus sólo con activos aportados explícitamente.
- [ ] Cancela y cierra con cada suite inactiva y activa; no quedan procesos propiedad de la app.
- [ ] Exporta y revisa un ZIP de diagnósticos.
- [ ] Instala una versión siguiente lado a lado sin alterar datos ni modelos.
- [ ] Revierte a la versión anterior conservando datos compatibles.
- [ ] Desinstala binarios sin borrar configuración, historiales ni artefactos.

## Publicación

- [ ] Revisión humana de licencias de código y checkpoints redistribuidos.
- [ ] Artefacto firmado y firma verificada, si existe identidad de firma aprobada.
- [ ] Canal, compatibilidad de esquema, política de downgrade y notas de versión declarados.
- [ ] Descarga y aplicación requieren una acción visible del usuario.

Las casillas no deben versionarse como aprobadas a partir de una prueba en la máquina de desarrollo.
Los resultados reales se registran por versión, sistema limpio y hash del artefacto.
