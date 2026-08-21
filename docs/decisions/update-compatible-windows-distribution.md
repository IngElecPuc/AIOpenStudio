# Contrato de distribución Windows actualizable

- Estado: aceptado antes de implementar el empaquetado.
- Fecha: 2026-08-21.

## Contexto

El candidato Windows debe poder recibir nuevas versiones sin perder configuraciones, historiales,
credenciales, modelos ni artefactos. Un ejecutable que escriba datos dentro de su directorio de
instalación o se reemplace mientras está activo impediría actualizaciones seguras y rollback.

## Contrato obligatorio

- Separar el directorio de aplicación, inmutable y reemplazable, del directorio de datos del usuario.
  En una distribución instalada, logs, SQLite, perfiles y outputs deben resolverse mediante
  `platformdirs`; las rutas relativas actuales son exclusivamente de desarrollo.
- Mantener modelos y runtimes externos fuera del paquete. Una actualización nunca borra, mueve ni
  vuelve a descargar Ollama, Fooocus, Whisper, pesos o la biblioteca compartida.
- Construir artefactos versionados y reproducibles. Cada entrega publica versión, canal, SHA-256,
  inventario de dependencias/licencias, compatibilidad de esquema y firma cuando exista una
  identidad de firma de código.
- Bloquear la candidatura cuando falte una dependencia inventariada, una licencia quede sin
  resolver o los textos/avisos legales no estén incluidos. La metadata automatizada ayuda a
  inventariar, pero no reemplaza la revisión humana de términos de redistribución.
- Aplicar actualizaciones con AIOpenStudio cerrado, mediante un proceso separado. La instalación
  debe ser atómica o lado a lado: preparar, verificar y activar; nunca sobrescribir parcialmente una
  versión en ejecución.
- Conservar al menos la versión anterior hasta aprobar el primer arranque. Permitir rollback cuando
  la migración de datos sea compatible.
- Respaldar antes de una migración irreversible y declarar explícitamente cuando una versión no
  permita downgrade. Las migraciones deben ser idempotentes y registrar su revisión.
- No modificar `.env`, perfiles locales, secretos de keyring ni preferencias sin una migración
  versionada. Nunca incluir configuración del equipo de build en el artefacto.
- No fijar nombres de usuario, `USERPROFILE`, raíces de repositorio ni rutas de desarrollador en
  código, defaults o recursos empaquetados. El build debe inspeccionar contenido ASCII/UTF-16 y
  fallar ante cualquier `C:\Users\<usuario>` o valor privado aportado por la máquina de build.
- Excluir por construcción `.env`, `.vscode`, `.git`, `data`, bases, perfiles, logs, outputs y
  cachés. Esta barrera no admite excepciones por conveniencia.
- Los scripts de modelos deben usar un default relativo portable o exigir/aceptar una carpeta
  elegida explícitamente; nunca deben heredar una ruta personal del desarrollador.
- No descargar ni instalar una actualización silenciosamente. La comprobación, descarga y
  aplicación requieren acción visible del usuario hasta que exista una política posterior aprobada.
- Entregar guía offline y solución de problemas dentro del bundle para que diagnóstico,
  recuperación y ubicación de datos no dependan de conectividad.

## Consecuencias

El primer candidato debe priorizar un bundle Windows de carpeta versionada sobre un ejecutable
monolítico que se autoextraiga. La elección final de PyInstaller, MSI u otro instalador queda
subordinada a este contrato y a pruebas de actualización, rollback y desinstalación conservadora.
