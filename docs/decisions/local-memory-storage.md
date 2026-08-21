# Almacenamiento local de referencias y memoria

## Contexto

AIOpenStudio debe recordar modelos disponibles, rutas de pesos, conversaciones, mensajes y resúmenes. Los archivos de modelos, audio e imágenes pueden ser grandes o sensibles, y una futura capacidad RAG necesitará búsqueda textual y posiblemente vectorial.

## Decisión

- Usar SQLite como almacenamiento local predeterminado para metadatos y memoria de conversaciones.
- Usar FTS5 para búsqueda textual de mensajes y resúmenes.
- Mantener `sqlite-vec` como extensión opcional, desactivada hasta conocer el modelo de embeddings y sus dimensiones.
- Guardar pesos, audios e imágenes como archivos; SQLite conserva sus rutas y metadatos, no sus bytes.
- Permitir rutas absolutas externas mediante `.env` o rutas relativas bajo `data/`, que está ignorado por Git.
- Mantener PostgreSQL como integración opcional para escenarios que necesiten una base compartida o más robusta.
- Mantener conversaciones, mensajes, resúmenes y referencias de contexto exclusivamente en SQLite
  mientras no exista una política de privacidad separada y aceptada. Elegir PostgreSQL principal
  para configuraciones y ejecuciones no replica contenido conversacional de forma implícita.

## Consecuencias

- La aplicación puede funcionar sin un servidor de base de datos.
- FTS5 es una capacidad obligatoria y se comprueba al inicializar el almacenamiento.
- `sqlite-vec` debe cargarse por conexión. La carga de extensiones se habilita durante esa operación y se deshabilita inmediatamente después.
- La API pre-1.0 de `sqlite-vec` obliga a fijar la versión y revisar cualquier actualización.
- Los servicios deben ejecutar operaciones SQLite fuera del hilo principal de Tkinter.
- Las copias de seguridad deben incluir el archivo SQLite y, por separado, los directorios de activos que se quieran conservar.
- Archivar conserva contenido; eliminar una conversación borra en cascada sus mensajes, resúmenes y
  referencias administradas, pero nunca elimina archivos externos del usuario.

## Fuentes

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [sqlite-vec para Python](https://alexgarcia.xyz/sqlite-vec/python.html)
- [Repositorio y licencia de sqlite-vec](https://github.com/asg017/sqlite-vec)
