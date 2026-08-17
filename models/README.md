# Model manifests

Store small, versioned manifests and per-runtime configuration here. Do not commit model weights or caches. A manifest may reference a weight path configured externally, but must not contain secrets.

- `llm/`: LLM metadata independent of the Ollama adapter.
- `fooocus/`: Fooocus model/style manifests.
- `whisper/`: Whisper variant manifests.
