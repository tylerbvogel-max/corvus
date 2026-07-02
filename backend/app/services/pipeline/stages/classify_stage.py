"""Classify stages: full (LLM), cheap (embed-only), adaptive (escalating).

plat-cheap-recall: the per-query LLM classify call is a latency + cost tax
when an external agent recalls memory every turn. Cheap mode reproduces the
classify outputs without a model — the graph classifies itself via neighbor
vote. Adaptive mode is recognition-first (System 1) with LLM deliberation
(System 2) only when recognition is uncertain.
"""

from typing import Any

from app.config import settings
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class ClassifyStage:
    """Embed + LLM classify (concurrent via asyncio).

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


class CheapClassifyStage:
    """Embed-only classification — zero LLM calls, zero marginal cost.

    Keywords come from the stopword-filtered tokenizer; region/role tags
    from a similarity-weighted vote over the top-k nearest neurons; intent
    stays neutral (it only drives the closing-format instruction, and a
    neutral default is acceptable for the agent-memory use case).

    Reads:  state.user_message
    Writes: same fields as ClassifyStage (classify_result carries
            recall_mode + neighbor_top_similarity for telemetry/escalation)
    """

    name = "classify"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.executor import _embed_query_async, _neighbor_vote_classify
        from app.services.scoring_engine import extract_keywords

        query_embedding = None
        try:
            query_embedding = await _embed_query_async(state.user_message)
        except Exception as e:  # embedding model failure -> keyword-only recall
            print(f"Cheap recall embedding failed, keyword-only fallback: {e}")

        regions: list[str] = []
        role_keys: list[str] = []
        top_similarity = 0.0
        if query_embedding is not None:
            regions, role_keys, top_similarity = await _neighbor_vote_classify(
                ctx.db, query_embedding,
            )

        state.classify_result = {
            "classification": {
                "intent": "general_query",
                "departments": regions,
                "role_keys": role_keys,
                "keywords": [],
            },
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "recall_mode": "cheap",
            "neighbor_top_similarity": round(top_similarity, 4),
        }
        state.query_embedding = query_embedding
        state.intent = "general_query"
        state.departments = regions
        state.role_keys = role_keys
        state.keywords = extract_keywords(state.user_message)
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "recall_mode": out.classify_result.get("recall_mode"),
            "neighbor_top_similarity": out.classify_result.get("neighbor_top_similarity"),
            "departments": out.departments,
            "role_keys": out.role_keys,
            "keywords": out.keywords,
        }


class AdaptiveClassifyStage:
    """Cheap recall first; escalate to LLM classify only when uncertain.

    Recognition (fast, automatic) handles the common case; deliberation
    (LLM) fires only when the top neighbor similarity is below
    settings.cheap_recall_confidence_threshold. The escalation re-embeds
    the query (~ms, local) — negligible next to the LLM call it precedes.
    """

    name = "classify"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        state = await CheapClassifyStage().run(state, ctx)
        top_similarity = state.classify_result.get("neighbor_top_similarity", 0.0)
        if top_similarity >= settings.cheap_recall_confidence_threshold:
            state.classify_result["recall_mode"] = "adaptive:cheap"
            return state

        state = await ClassifyStage().run(state, ctx)
        state.classify_result["recall_mode"] = "adaptive:full"
        state.classify_result["neighbor_top_similarity"] = round(top_similarity, 4)
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "recall_mode": out.classify_result.get("recall_mode"),
            "neighbor_top_similarity": out.classify_result.get("neighbor_top_similarity"),
            "intent": out.intent,
            "departments": out.departments,
            "role_keys": out.role_keys,
            "keywords": out.keywords,
        }
