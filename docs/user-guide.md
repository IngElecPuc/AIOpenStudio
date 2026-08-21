# Guía de usuario

## Inicio seguro

1. Inicia AIOpenStudio con el acceso de la distribución o, durante desarrollo, con
   `& .\.venv\Scripts\python.exe -m aiopenstudio`.
2. Abre `Monitor` y comprueba RAM, VRAM y runtimes disponibles antes de cargar un modelo pesado.
3. Usa sólo modelos que ya estén instalados. Ningún tab descarga pesos automáticamente.
4. Cierra la ventana principal y espera el apagado ordenado antes de apagar Windows o borrar datos.

La ausencia de PostgreSQL, Fooocus o Whisper degrada únicamente la capacidad correspondiente. El
modo básico con SQLite y las demás suites debe seguir iniciando.

## LLM y conversaciones

El tab `LLM` obtiene sus modelos desde Ollama. Permite cargar, conversar por streaming, cancelar y
liberar el modelo. El dictado cede temporalmente la GPU a Whisper cuando es necesario.

Las conversaciones se guardan en la base SQLite local. PostgreSQL conserva metadatos de ejecución,
pero el contenido conversacional permanece local. Si un modelo instalado no aparece:

```powershell
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

Después de cambiar la biblioteca administrada por Ollama, reinicia AIOpenStudio para reconciliar el
catálogo.

## Whisper y transcripciones

El tab `Whisper` abre un audio o captura el micrófono, selecciona un modelo local y exporta TXT,
JSON, SRT o VTT. La selección visible y el modelo residente son estados diferentes; cambiar de
modelo descarga el anterior cuando corresponde.

Las grabaciones temporales y resultados viven fuera de la base de datos. Conserva únicamente las
exportaciones que necesites y no compartas el ZIP de diagnósticos como sustituto del audio original.

## Fooocus e imágenes

El tab `Fooocus` administra una cola FIFO, progreso, cancelación y galería. Cada ejecución aceptada
crea una carpeta propia con imágenes y metadatos. Además de texto a imagen, expone variaciones,
upscale, inpaint/outpaint, Image Prompt, PyraCanny, CPDS, FaceSwap, Describe y Enhance. Las entradas
se validan, copian y normalizan dentro de la ejecución antes de enviarse al proceso aislado.

Fooocus se ejecuta en un entorno Python aislado. No actives ese entorno para iniciar AIOpenStudio y
no uses el actualizador upstream desde la aplicación. Consulta la
[guía de uso de Fooocus](fooocus-user-guide.md) para aprender cada parámetro, administrar
referencias y seguir recetas completas. Las capacidades que necesiten un activo local ausente se
bloquean antes de utilizar GPU y nunca deben descargarlo implícitamente.

## Monitor y memoria

`Monitor` distingue proceso, modelo en RAM y residencia GPU. Antes de una operación pesada:

- conserva al menos 6 GiB de RAM libre;
- evita ejecutar más de una carga GPU pesada simultánea;
- cancela o libera modelos inactivos si se alcanza un umbral crítico;
- no confundas tamaño del archivo con consumo final de RAM o VRAM.

Una validación con OOM deliberado requiere supervisión y autorización separada.

## Persistencia

`Configuración` ofrece tres modos:

- `Solo SQLite`: funcionamiento local sin PostgreSQL.
- `SQLite + réplica PostgreSQL`: SQLite recibe las escrituras y PostgreSQL conserva una réplica.
- `PostgreSQL principal`: escribe en PostgreSQL conectado y activa un fallback SQLite advertido si
  se pierde la conexión.

La preferencia PostgreSQL no se cambia silenciosamente después de un fallo. Abre
`Configuración → Conexión PostgreSQL…` para reconectar o seleccionar manualmente otro modo. La
contraseña sólo debe vivir en una variable de entorno o en el almacén seguro del sistema.

## Datos y privacidad

Durante desarrollo, las rutas relativas se resuelven desde el repositorio. En una distribución
Windows, `platformdirs` separa configuración y datos del directorio reemplazable de la aplicación.
Una ruta absoluta elegida por el usuario se conserva.

| Recurso | Ubicación lógica |
|---|---|
| Conversaciones | SQLite local |
| Transcripciones | salida elegida o `data/outputs/whisper` en desarrollo |
| Imágenes | `data/outputs/fooocus` en desarrollo |
| Logs | directorio de datos de usuario; `data/logs` en desarrollo |
| Modelos | `data/models` o carpeta elegida explícitamente |
| Perfil PostgreSQL | configuración local sin contraseña |

No copies `.env`, bases SQLite, perfiles, modelos, audios o imágenes dentro del directorio de una
distribución.

## Diagnósticos y soporte

`Configuración → Diagnósticos…` recopila sistema, rutas, runtimes y persistencia fuera del hilo de
Tkinter. `Exportar ZIP redactado…` incluye el snapshot y colas acotadas de logs. Excluye bases,
modelos, prompts, respuestas, audios e imágenes.

Revisa siempre el ZIP antes de compartirlo. Para interpretar errores y estados, continúa con
[solución de problemas](troubleshooting.md).

## Actualizaciones

Una actualización no debe tocar datos, modelos ni configuración. El candidato Windows usa un
artefacto versionado, manifiesto SHA-256 y rutas de usuario separadas. Hasta que exista un activador
firmado y aprobado, ninguna actualización se descarga o aplica silenciosamente.
