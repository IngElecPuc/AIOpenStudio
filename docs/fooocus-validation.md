# Validación de la suite Fooocus

> Estado: preflight, generación y cancelación reales aprobados el 20 de agosto de 2026. Fooocus
> v2.5.5 corre en Python 3.10.11 aislado con PyTorch 2.7.1+cu128 sobre la RTX 5060 capability 12.0.
> El transporte usa la respuesta Gradio sin deserialización defectuosa, confina temporales al
> runtime y verifica cada imagen antes de aislarla por ejecución. La cancelación fue validada tanto
> durante la carga como durante el sampler. Ningún comando de validación descarga Fooocus ni
> modelos.

## Resultados nativos aprobados

- Generación `f91a07f9-09e5-4044-92ce-08f94f2bae69`: `completed` en 39,05 s, una imagen PNG
  1024×1024, seed 12345 y sin advertencias.
- Cancelación durante carga `439d19cd-210c-49b1-8bdd-befc64afb9cb`: `cancelled` en 9,15 s, sin
  imágenes ni advertencias.
- Cancelación durante sampler `9c9426f1-92fa-4204-bd4f-3286f4019d03`: `cancelled`, un único
  evento terminal y sin imágenes.

Quedan por validar la concurrencia con LLM/Whisper, la experiencia completa del tab y el manejo de
OOM deliberado.

## Pruebas seguras

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy src
```

Cubren contratos, validación de parámetros, descubrimiento dinámico del esquema Gradio, ciclo del
runtime, aislamiento y verificación de imágenes, metadatos y servicio encolado mediante fakes. No
inician procesos externos ni usan GPU.

## Layout aportado por el usuario

La aplicación espera, de forma configurable, una instalación en
`data/runtime/fooocus/app`, su intérprete aislado en
`data/runtime/fooocus/env/Scripts/python.exe` y checkpoints en
`<MODEL_LIBRARY_ROOT>/fooocus/checkpoints/`. Fooocus y sus pesos deben instalarse por una acción
separada y autorizada; AIOpenStudio no ejecuta el actualizador.

Para un arranque sin red también deben existir bajo la biblioteca Fooocus:

- `vae_approx/xlvaeapp.pth`
- `vae_approx/vaeapp_sd15.pth`
- `vae_approx/xl-to-v1_interposer-v4.0.safetensors`
- `prompt_expansion/fooocus_expansion/pytorch_model.bin`

Los cuatro activos están declarados en el catálogo versionado con las URLs usadas por
`launch.py` de Fooocus v2.5.5. Se aportan explícitamente, sin iniciar Fooocus, mediante:

```powershell
& .\.venv\Scripts\python.exe scripts\model_library.py download `
  image.fooocus-xl-vae-approx `
  image.fooocus-sd15-vae-approx `
  image.fooocus-xl-to-v1-interposer `
  image.fooocus-prompt-expansion
```

El comando exige escribir `DESCARGAR`, usa archivos `.partial`, reanuda cuando el servidor admite
rangos y registra tamaño y SHA-256. Los nombres locales diferentes de la fuente
(`vaeapp_sd15.pth` y `pytorch_model.bin`) quedan fijados en el manifiesto.

Fooocus también necesita `config.json`, vocabulario y archivos de tokenizer junto al peso de
expansión. Esos siete archivos pequeños ya vienen en la fuente oficial v2.5.5: el supervisor los
copia de forma atómica a la ruta compartida al arrancar. Esta preparación es completamente local,
no instala paquetes ni accede a Internet.

El supervisor usa `--disable-preset-download`, deshabilita índices de `pip` y sustituye los comandos
de autoaprovisionamiento de `launch.py` por comprobaciones inocuas. Si falta una dependencia o uno
de estos activos, debe fallar el preflight en vez de descargarlo durante el arranque.

Las dependencias del entorno secundario se instalan, desde la raíz, con:

```powershell
& .\data\runtime\fooocus\env\Scripts\python.exe -m pip install -r .\requirements-fooocus.txt
```

Este contrato conserva los pins oficiales de v2.5.5 y agrega PyTorch 2.7.1/torchvision 0.22.1 con
CUDA 12.8 como combinación candidata para Blackwell. No debe instalarse en `.venv` y todavía debe
validarse con la GPU real. También fija FastAPI 0.101.0/Starlette 0.27.0 y sus dependencias de
servidor contemporáneas, porque Gradio 3.41.2 no les impone un límite superior y las versiones
actuales cambiaron el contrato de plantillas.

El entorno principal sólo necesita el cliente compatible con el Gradio usado por Fooocus:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[fooocus]"
```

Este comando instala paquetes y, por tanto, requiere autorización antes de ejecutarlo. No descarga
pesos. Para Fooocus v2.5.5 debe quedar exactamente `gradio-client==0.5.0`; el preflight rechaza
otras versiones para evitar mezclar protocolos de cola Gradio.

Cada arranque conserva la salida completa del proceso en
`data/runtime/fooocus/fooocus-process.log`. Cuando el cliente llega a leer el servidor también
persiste `data/runtime/fooocus/gradio-config.json`. Ambos son locales e ignorados por Git y permiten
diagnosticar un cierre CUDA o una divergencia de esquema sin repetir un run a ciegas.

## Preflight

```powershell
& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py preflight
```

Comprueba rutas, intérprete, `launch.py`, cliente Gradio y checkpoints sin iniciar Fooocus. Sólo
imprime consola y no crea un reporte. No ejecutar `smoke` hasta que termine con código cero y
`health: ready`:

```powershell
& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py preflight
if ($LASTEXITCODE -ne 0) { throw "Fooocus todavía no está listo para smoke" }
```

Si se invoca un run con requisitos faltantes, el validador crea únicamente un JSON con estado
`preflight_failed`; no encola trabajo, no crea el directorio de imágenes y no toma la GPU.

## Capacidades avanzadas: validación segura

El 21 de agosto de 2026 se contrastó el adaptador con la copia local del esquema Gradio v2.5.5,
sin iniciar una generación. El esquema identificó las once operaciones soportadas por el contrato,
cuatro tipos de referencia, cuatro ranuras Image Prompt y tres etapas Enhance. Las solicitudes
sintéticas de generación produjeron 152 argumentos por operación (sin contar el estado Gradio) y
Describe produjo tres, resueltos por identidad y orden de componentes en vez de índices expuestos a
la UI.

La batería segura terminó con `101 passed, 3 skipped`; `ruff check .` y `mypy src` terminaron sin
errores. Cubre validación de contratos, PNG/JPEG/BMP, rechazo de WEBP de entrada, normalización y
hashes, aislamiento de archivos, memoria/olvido de galería, descubrimiento de capacidades, mapeo
de argumentos y bloqueo previo de activos faltantes. Los siete modelos REMBG de máscara Enhance
están catalogados junto con SAM; `U2NET_HOME` se fija dentro de la biblioteca compartida. Si falta
el ONNX elegido, el preflight falla de forma localizada antes de que upstream pueda descargarlo en
el perfil del usuario.

El inventario versionado está en `models/fooocus/advanced-assets.json`. Sus tamaños y hashes
permanecen pendientes mientras los archivos no hayan sido adquiridos con autorización; el comando
`preflight` informa los valores reales de cualquier activo ya presente sin modificarlo.

El preflight local del 21 de agosto terminó con código cero y `schema_source: cached`: confirmó las
once operaciones, cuatro tipos/cuatro ranuras de referencia y tres etapas Enhance. Los 23 activos
avanzados catalogados resultaron ausentes (`size_bytes` y `sha256` nulos) y
`downloads_performed: false`. `health: ready` describe la vertical base; cada operación avanzada
que necesite uno de esos activos seguirá bloqueada por su propio preflight.

## Runs reales delegados

Sustituir el nombre por uno listado en el preflight:

```powershell
& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py smoke `
  --model "checkpoint.safetensors" `
  --prompt "a small red cabin in a snowy forest" `
  --seed 12345

& .\.venv\Scripts\python.exe scripts\validate_fooocus_vertical.py cancel `
  --model "checkpoint.safetensors" `
  --prompt "a detailed landscape" `
  --cancel-after 5
```

ETA de arranque: 30–300 segundos; generación: 1–15 minutos según checkpoint y parámetros. Cada run
crea exactamente un JSON bajo `data/outputs/fooocus-validation/` y sus imágenes aisladas bajo
`data/outputs/fooocus-validation/runs/<operation_id>/`. El reporte guarda hash del prompt, eventos,
tiempos y metadatos de artefactos, no el prompt. El validador imprime cada transición y un latido
cada 10 segundos durante esperas largas; además conserva en el JSON los eventos recibidos antes de
un error o timeout.

## Pasada manual y de presión

1. Iniciar la aplicación y confirmar que LLM, Monitor y Whisper siguen disponibles si Fooocus no lo
   está.
2. Encolar dos prompts y verificar orden FIFO, UI fluida, estados y galería.
3. Cancelar un trabajo activo y otro todavía en cola; comprobar cierre y recuperación de VRAM.
4. Con un LLM residente pero ocioso, generar y verificar traslado temporal, ejecución Fooocus y
   restauración del LLM.
5. Repetir mientras el LLM genera: Fooocus debe esperar y no interrumpir la respuesta.
6. Repetir con Whisper residente y verificar descarga/restauración.
7. Forzar un escenario que exceda VRAM y confirmar error visible, metadata `failed`, proceso
   recuperado y aplicación utilizable.
8. Abrir `metadata.json`, comparar hashes/dimensiones y confirmar que ningún archivo temporal se
   presenta como output final.
