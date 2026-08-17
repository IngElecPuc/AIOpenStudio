# Ollama como runtime externo de la suite LLM

- **Estado:** aceptada y validada para la primera vertical.
- **Fecha:** 17 de agosto de 2026.

## Decisión

La suite visual se denomina LLM y depende de `LLMService`. Ollama es un adaptador concreto en
`infrastructure/runtimes/ollama`; ni la UI ni los contratos de `core` importan su SDK.

AIOpenStudio se conecta a un servidor Ollama ya iniciado. No instala, inicia ni detiene su proceso,
y no llama las operaciones `pull` o `delete`. El catálogo se reconcilia mediante `/api/tags`; antes
de cargar o inferir se comprueba que el modelo ya esté instalado, evitando descargas implícitas.

La carga explícita usa una generación vacía con `keep_alive`; la liberación completa usa
`keep_alive=0`. Chat usa streaming y la cancelación cancela la tarea asíncrona activa. Las
conversaciones y respuestas terminadas o parcialmente canceladas se guardan en SQLite local.

## Límites conocidos

Ollama decide automáticamente el reparto CPU/GPU. Su API no ofrece, para esta integración, mover de
forma independiente un modelo entre RAM y GPU. Por eso el adaptador declara que no soporta selección
de dispositivo ni descarga parcial y rechaza esas acciones con un error explícito. Los bytes de RAM
y VRAM son la interpretación directa de `size` y `size_vram` entregados por `/api/ps`, no una medición
propia del sistema operativo.

La opción de mantener un modelo residente fija `keep_alive=-1` para el modelo completo. Liberar RAM
o GPU por separado quedará disponible sólo en runtimes futuros que realmente expongan esa capacidad.

## Consecuencias

- Es posible reemplazar Ollama o sumar otro backend sin rediseñar el tab LLM.
- La aplicación inicia aunque Ollama no responda; el tab informa el error sin afectar suites futuras.
- Todo I/O de Ollama se ejecuta en un bucle asíncrono fuera del hilo principal de Tkinter.
- Las pruebas unitarias usan dobles y las pruebas contra Ollama son optativas.
- Las ejecuciones con un modelo real se delegan al usuario y producen reportes diagnósticos únicos.
