"""Provider ids, backend names, and the default backend of each provider.

A stage names a provider; unless the stage pins ``backend`` explicitly, the backend is the
one listed here. Each backend renderer under ``stagr/core/renderers/`` declares the same
provider and backend strings as its class attributes.
"""
from __future__ import annotations

PROVIDER_OPENAI = "openai"

BACKEND_CODEX = "codex"
# Codex run inside the pipeline on an OpenAI API key instead of through the Codex GitHub App.
BACKEND_CODEX_API = "codex-api"

DEFAULT_BACKEND_BY_PROVIDER: dict[str, str] = {
    PROVIDER_OPENAI: BACKEND_CODEX,
}
