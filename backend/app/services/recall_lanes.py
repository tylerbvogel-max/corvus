"""Hybrid-recall retrieval lanes: keyword (tsvector) and entity match.

Node mind-hybrid-recall (2026-07-14, the Mem0 retrieval lesson): named-thing
queries ("Becoming Nicole") lose the pure-cosine race because titles and
proper nouns drown in dialogue-soup embeddings. These lanes retrieve by
lexical and entity match, Postgres-native, zero LLM in the hot path; the
ranked lists are fused with the embedding lane via reciprocal-rank fusion
in score_candidates. Both lanes are gated by settings flags (default off)
so embed-only behavior is unchanged until the A/B says otherwise.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Must match the expression index ix_neurons_content_tsv (see main.py
# _migrate_neuron_and_query_columns) or Postgres won't use the index.
TSV_EXPR = ("to_tsvector('english', coalesce(label, '') || ' ' || "
            "coalesce(content, '') || ' ' || coalesce(summary, ''))")

# Sentence-initial capitals in questions are grammar, not entities.
_ENTITY_STOP = frozenset(
    "what when where which who whom whose why how did does do is are was were "
    "the a an in on at of for and or but if then has have had can could would "
    "should will i you he she it we they my your his her its our their this "
    "that these those there here".split()
)
_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{2,80})[\"'”’]")
_CAP_TOKEN_RE = re.compile(r"^[A-Z][a-zA-Z0-9'\-]*$")


def normalize_entities(raw: list | None, max_entities: int = 12) -> list[str]:
    """Lowercase, strip, dedupe, and bound an entity list for storage."""
    assert max_entities > 0, "max_entities must be positive"
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        ent = str(item).strip().lower()[:80]
        if len(ent) < 2 or ent in seen:
            continue
        seen.add(ent)
        out.append(ent)
        if len(out) >= max_entities:
            break
    return out


def extract_query_entities(query: str) -> list[str]:
    """Heuristic (no-LLM) entity extraction from a query: quoted spans plus
    runs of capitalized tokens whose lowercase form isn't question grammar."""
    assert isinstance(query, str), "query must be a string"
    entities: list[str] = list(_QUOTED_RE.findall(query))

    run: list[str] = []
    for raw in query.replace("?", " ").replace(",", " ").split():
        token = raw.strip(".;:!()[]{}")
        if _CAP_TOKEN_RE.match(token) and token.lower() not in _ENTITY_STOP:
            run.append(token)
        else:
            if run:
                entities.append(" ".join(run))
            run = []
    if run:
        entities.append(" ".join(run))
    return normalize_entities(entities)


async def keyword_lane(
    db: AsyncSession, query_text: str, top_n: int = 50,
) -> dict[int, float]:
    """Full-text lane: ts_rank_cd over label+content+summary (stemmed, so
    'attending' matches 'attend'). Returns {neuron_id: rank_score}."""
    from app.services.scoring_engine import extract_keywords
    assert top_n > 0, "top_n must be positive"
    # OR semantics: AND-of-all-terms (websearch_to_tsquery default) returns
    # nothing for natural questions; ts_rank_cd still rewards records that
    # match MORE of the query's distinctive terms.
    tokens = [re.sub(r"[^a-z0-9]", "", kw.lower())
              for kw in extract_keywords(query_text or "")]
    tokens = [t for t in tokens if len(t) >= 3]
    if not tokens:
        return {}
    sql = f"""
        SELECT id, ts_rank_cd({TSV_EXPR}, q) AS score
        FROM neurons, to_tsquery('english', :query) q
        WHERE {TSV_EXPR} @@ q AND is_active = true
        ORDER BY score DESC
        LIMIT :top_n
    """
    result = await db.execute(
        text(sql), {"query": " | ".join(tokens), "top_n": top_n})
    return {row[0]: float(row[1]) for row in result.all()}


async def entity_lane(
    db: AsyncSession, query_entities: list[str], top_n: int = 50,
) -> dict[int, float]:
    """Entity lane: match query entities against the write-time-extracted
    neurons.entities array. Exact match weighs 1.0, query-contained-in-stored
    substring match ("nicole" -> "becoming nicole") 0.5, and every
    match is scaled by the stored entity's IDF. Matches on entities present
    in more than a quarter of the corpus are DROPPED, not just down-weighted:
    RRF fusion is rank-based, so a ubiquitous entity (a speaker name with
    df 196/316 on LoCoMo conv0) would fill the lane with tied rows whose
    arbitrary ordering injects pure rank noise into the fusion. The lane
    stays silent unless the query names something discriminative.
    Returns {neuron_id: summed idf-weighted score}."""
    assert top_n > 0, "top_n must be positive"
    ents = normalize_entities(query_entities)
    if not ents:
        return {}
    sql = """
        WITH corpus AS (
            SELECT id, lower(e.ent) AS sent
            FROM neurons, LATERAL jsonb_array_elements_text(entities) AS e(ent)
            WHERE is_active = true AND entities IS NOT NULL
        ),
        df AS (
            SELECT sent, count(DISTINCT id) AS d,
                   (SELECT count(DISTINCT id)::float FROM corpus) AS n
            FROM corpus GROUP BY sent
        ),
        matches AS (
            SELECT c.id, c.sent,
                   MAX(CASE WHEN c.sent = qe.ent THEN 1.0
                            WHEN c.sent LIKE '%' || qe.ent || '%' THEN 0.5
                            ELSE 0.0 END) AS w
            FROM corpus c, unnest(CAST(:ents AS text[])) AS qe(ent)
            GROUP BY c.id, c.sent
            HAVING MAX(CASE WHEN c.sent = qe.ent THEN 1.0
                            WHEN c.sent LIKE '%' || qe.ent || '%' THEN 0.5
                            ELSE 0.0 END) > 0
        )
        SELECT m.id, SUM(m.w * ln(1 + df.n / df.d)) AS score
        FROM matches m JOIN df ON df.sent = m.sent
        WHERE df.d / df.n <= 0.25
        GROUP BY m.id
        ORDER BY score DESC
        LIMIT :top_n
    """
    result = await db.execute(text(sql), {"ents": ents, "top_n": top_n})
    return {row[0]: float(row[1]) for row in result.all()}
