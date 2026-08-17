# Model manifests

Store small, versioned manifests and per-runtime configuration here. Do not commit model weights or caches. A manifest may reference a weight path configured externally, but must not contain secrets.

- `llm/`: LLM metadata independent of the Ollama adapter.
- `fooocus/`: Fooocus model/style manifests.
- `whisper/`: Whisper variant manifests.

`download-catalog.json` is the versioned candidate catalog consumed by
`scripts/model_library.py`. Listing or importing it has no side effects. An entry describes a
possible download; it does not mean that the artifact is installed or approved automatically.
