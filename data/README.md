# Datos locales

Este directorio está ignorado por Git, excepto este archivo. La estructura predeterminada se crea solo cuando una operación la necesita:

- `inputs/`: archivos elegidos como entrada.
- `outputs/`: resultados exportados.
- `models/`: pesos administrados por AIOpenStudio.
- `cache/`: cachés, incluido `cache/huggingface/`.
- `runtime/`: SQLite y estado temporal de runtimes.
- `logs/`: logs rotativos sin prompts ni secretos por defecto.

Cualquiera de estas rutas puede apuntar fuera del repositorio mediante `.env`. No guardar aquí credenciales.
