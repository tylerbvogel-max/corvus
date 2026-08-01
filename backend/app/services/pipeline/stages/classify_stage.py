"""Classify stage: embed-only classification (zero LLM calls).

plat-cheap-recall: the per-query LLM classify call was a latency + cost tax and,
on evaluation, lower-performance than this free path — so it was removed entirely.
The graph classifies itself via a similarity-weighted neighbor vote; keywords come
from the stopword-filtered tokenizer.
"""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class CheapClassifyStage:
    """Embed-only classification — zero LLM calls, zero marginal cost.

    Keywords come from the stopword-filtered tokenizer; region/role tags from a
    similarity-weighted vote over the top-k nearest neurons; intent stays neutral
    (it only drives the closing-format instruction).

    Reads:  state.user_message
    Writes: classify_result, query_embedding, intent, departments, role_keys, keywords
    """

    name = "classify"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.recall_primitives import _embed_query_async, _neighbor_vote_classify
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
