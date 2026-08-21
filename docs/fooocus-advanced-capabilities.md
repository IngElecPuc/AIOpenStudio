# Capacidades avanzadas de Fooocus

La integración avanzada conserva Fooocus v2.5.5 como proceso aislado y su esquema Gradio como
detalle de infraestructura. AIOpenStudio descubre el esquema en vivo o usa la última copia local;
la instalación auditada expone cuatro referencias de Image Prompt, tres etapas Enhance y las
operaciones de variación, upscale, inpaint/outpaint, Describe y Enhance.

## Entradas y privacidad

- Se aceptan por ahora PNG, JPEG y BMP. La extensión y el contenido real deben coincidir.
- Cada entrada habilitada se copia en `inputs/originals` dentro de la ejecución y se normaliza a
  PNG RGB/RGBA en `inputs/normalized`; Fooocus sólo recibe la copia normalizada.
- Se rechazan formatos no habilitados, archivos multipágina/animados, imágenes dañadas, entradas
  sobre el límite de bytes y más de 40 millones de píxeles por defecto.
- `inputs/manifest.json` registra nombre, formato, dimensiones, tamaño y SHA-256 del original y de
  la copia normalizada, pero no conserva la ruta de origen del usuario.
- La cola de referencias es transitoria. Los elementos se pueden ordenar, deshabilitar, quitar y
  previsualizar; cada uno controla tipo, `Stop At` y peso.

## Galería

La galería de la sesión permite miniaturas, vista ampliada y navegación. Por defecto no se
reconstruye tras reiniciar. Al activar **Recordar índice entre reinicios** se guarda únicamente un
índice de rutas bajo el directorio de outputs; no se duplican imágenes. **Olvidar galería** elimina
ese índice y las miniaturas de la UI, pero nunca borra los outputs. La eliminación de imágenes
deberá seguir siendo una acción distinta y confirmada.

## Activos y bloqueo de red

El registro versionado `models/fooocus/advanced-assets.json` identifica origen, finalidad y estado
de licencia de cada activo que upstream intentaría obtener. Antes de cargar la GPU, el preflight de
la operación comprueba exactamente los activos requeridos y detiene el trabajo si falta alguno.
Fooocus conserva `HF_HUB_OFFLINE=1`, proxies inválidos y descargas de presets deshabilitadas.
Los siete modelos ONNX de REMBG se resuelven exclusivamente desde `fooocus/rembg/` en la biblioteca
compartida mediante `U2NET_HOME`; nunca se delegan a la caché `~/.u2net` del usuario.

El comando siguiente no inicia Fooocus ni descarga nada; informa presencia, tamaño y SHA-256 de
los activos avanzados que ya existan:

```powershell
python scripts/validate_fooocus_vertical.py preflight
```

Un activo faltante no tiene todavía tamaño ni hash local. No se considera incorporado: su licencia
debe resolverse, su descarga debe autorizarse explícitamente y el reporte posterior debe registrar
tamaño y SHA-256 antes de habilitar su primera validación real.

## Matriz pendiente de hardware

La implementación y las pruebas seguras no sustituyen la matriz manual. Siguen pendientes, sin
afirmar éxito: un run real por operación y tipo de referencia; formatos y referencias múltiples;
galería persistente/transitoria; cancelación y cola FIFO; intercambio con LLM y Whisper; ausencia
de descargas; caída del worker; y recuperación frente a OOM deliberado autorizado.
