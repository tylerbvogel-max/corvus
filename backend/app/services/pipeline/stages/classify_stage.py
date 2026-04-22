"""Embed the query and run intent/departments classification in parallel."""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class ClassifyStage:
    """Embed + classify (concurrent via asyncio).

    Reads:  state.user_message
    Writes: state.classify_result, query_embedding, intent, departments, role_keys, keywords
    """

    name = "classify"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        # Import inside run to keep the stage module lightweight at startup.
        from app.services.executor import _embed_and_classify
        (
            classify_result,
            query_embedding,
            intent,
            departments,
            role_keys,
            keywords,
        ) = await _embed_and_classify(state.user_message)
        state.classify_result = classify_result
        state.query_embedding = query_embedding
        state.intent = intent
        state.departments = departments
        state.role_keys = role_keys
        state.keywords = keywords
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "intent": out.intent,
            "departments": out.departments,
            "role_keys": out.role_keys,
            "keywords": out.keywords,
        }
