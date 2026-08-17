import asyncio
import os

import pytest

from aiopenstudio.core.contracts import RuntimeHealth
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime

RUN_INTEGRATION = os.getenv("AIOPENSTUDIO_RUN_OLLAMA_INTEGRATION") == "1"


@pytest.mark.ollama_integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason="Ollama integration is opt-in")
def test_local_ollama_health_and_catalog() -> None:
    async def scenario() -> None:
        runtime = OllamaRuntime(os.getenv("AIOPENSTUDIO_OLLAMA_BASE_URL", "http://localhost:11434"))
        try:
            assert await runtime.health() is RuntimeHealth.READY
            assert await runtime.list_models()
        finally:
            await runtime.close()

    asyncio.run(scenario())
