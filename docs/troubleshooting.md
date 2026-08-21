# Solución de problemas

## La aplicación no inicia

Durante desarrollo, valida primero el entorno sin instalar paquetes adicionales:

```powershell
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -c "import aiopenstudio; print(aiopenstudio.__version__)"
```

Debe usarse Python 3.11 o 3.12 x64. Conserva el traceback completo y evita reconstruir `.venv` si
las tres comprobaciones funcionan.

## Ollama no responde o no muestra modelos

```powershell
Get-Service ollama* -ErrorAction SilentlyContinue
Invoke-RestMethod http://127.0.0.1:11434/api/version
ollama list
ollama ps
```

- El servicio normal usa loopback y puerto 11434.
- Un servidor temporal de la biblioteca puede usar otro puerto; no debe apropiarse de un puerto ya
  ocupado ni cambiar silenciosamente `OLLAMA_MODELS` del servicio normal.
- Si un modelo aparece en `ollama list` pero no en la UI, reinicia la aplicación para reconciliar
  el catálogo.

## Whisper no está disponible

- Confirma que el snapshot local esté completo; la UI no descarga modelos.
- Revisa que `faster-whisper`, CTranslate2, PyAV y `sounddevice` estén en `.venv`.
- Un fallo CUDA puede permitir todavía CPU si el modelo y la memoria lo admiten.
- Si el worker cae repetidamente, se detendrá después del presupuesto de reinicios y requerirá una
  revisión de Diagnósticos.

## El micrófono no funciona

- Comprueba los permisos de micrófono de Windows y el dispositivo predeterminado.
- Cierra aplicaciones que mantengan el dispositivo en modo exclusivo.
- Cancela la captura antes de cerrar la aplicación; el cierre ordenado también intenta detenerla.
- No instales FFmpeg para resolver un problema de captura: faster-whisper usa PyAV y no lo requiere.

## Fooocus no está disponible

Ejecuta el preflight sin descargar nada:

```powershell
& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py preflight
```

Revisa por separado fuente fijada, Python 3.10 aislado, versiones del servidor Gradio, activos
offline y checkpoints. La ausencia de Fooocus no debe bloquear LLM, Monitor o Whisper.

## PostgreSQL rechaza la conexión

Errores `permission denied for schema public` indican que el rol puede conectar a la base, pero no
crear las tablas o `alembic_version` en el esquema. La base dedicada debería pertenecer al rol de la
aplicación o concederle permisos explícitos sobre el esquema.

También comprueba servidor, puerto, base, rol, contraseña, SSL y `pg_hba.conf`. No pongas la
contraseña en una URL, comando, log o captura. Con PostgreSQL desconectado, AIOpenStudio informa el
fallback SQLite y conserva la preferencia hasta que la cambies manualmente.

## Presión de memoria u OOM

1. Cancela la operación actual.
2. Revisa RAM/VRAM y procesos en `Monitor`.
3. Libera modelos residentes que no necesites.
4. Ejecuta una sola carga pesada GPU.
5. Reduce modelo o carga de trabajo antes de reintentar.

No repitas un OOM deliberadamente. Las pruebas automatizadas usan presión sintética sin reservar
memoria real; una recuperación OOM real requiere un procedimiento supervisado.

## Cierre incompleto o proceso caído

El cierre central bloquea acciones nuevas, cancela operaciones y detiene suites con timeouts. Si un
worker nativo no termina, Diagnósticos y los logs registran el paso fallido. En el siguiente inicio,
las ejecuciones que quedaron `queued` o `running` se reconcilian como `interrupted`.

Whisper y Fooocus permiten un máximo predeterminado de tres reinicios en cinco minutos. Superado el
límite, la suite se degrada en vez de mantener un ciclo de crashes.

## Generar un paquete de soporte

1. Abre `Configuración → Diagnósticos…`.
2. Pulsa `Actualizar` y revisa los estados.
3. Exporta el ZIP a una carpeta elegida.
4. Abre el ZIP y confirma que no contiene información que no quieras compartir.

El paquete no debe contener `.env`, bases, perfiles PostgreSQL, modelos, prompts, respuestas,
audios ni imágenes. Si encuentras alguno, no compartas el archivo y conserva el caso para corregir
la barrera de redacción.

## El bundle Windows fue rechazado

El build se detiene si detecta rutas de perfil, configuración local, datos, licencias sin resolver o
un manifiesto/hash inconsistente. No desactives la barrera ni agregues excepciones para publicar. La
solución es eliminar la contaminación del artefacto o completar el inventario de licencias.
