"""In-memory stand-in for llm_client.py. Makes no provider calls.

Listing and probing both always succeed: the demo's provider step exists to
let the reader make one real choice, not to exercise failure paths.
"""

from __future__ import annotations

from demo.content import DEMO_VERTEX_PROJECT, GEMINI_MODELS, GROQ_MODELS, VERTEX_MODELS
from llm_client import (  # noqa: F401  (re-exported for isinstance checks)
    LlmModelProbed,
    LlmModelsListed,
    VertexModelsListed,
    VertexProjectsListed,
)


async def list_gemini_models(api_key: str) -> LlmModelsListed:
    return LlmModelsListed(models=list(GEMINI_MODELS))


async def list_groq_models(api_key: str) -> LlmModelsListed:
    return LlmModelsListed(models=list(GROQ_MODELS))


async def list_vertex_models(
    service_account_key_b64: str, project: str | None = None, location: str | None = None
) -> VertexModelsListed:
    return VertexModelsListed(
        project_id=project or DEMO_VERTEX_PROJECT, models=list(VERTEX_MODELS)
    )


async def list_accessible_projects(*args, **kwargs) -> VertexProjectsListed:
    return VertexProjectsListed(projects=[DEMO_VERTEX_PROJECT])


async def probe_gemini_model(api_key: str, model: str) -> LlmModelProbed:
    return LlmModelProbed(model=model)


async def probe_vertex_model(
    service_account_key_b64: str, model: str,
    project: str | None = None, location: str | None = None,
) -> LlmModelProbed:
    return LlmModelProbed(model=model)
