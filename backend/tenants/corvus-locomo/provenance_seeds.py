"""Provenance seed data for the agentic-memory domain.

Sources map to the memory admission ladder (design §8.2 Amendment B):
informational (raw capture) -> guidance (evidence-backed) ->
organizational (user-confirmed).
"""

SEED_SOURCES: list[dict] = [
    {"canonical_id": "episode-log", "family": "EPISODE", "authority_level": "informational",
     "issuing_body": "harness hooks", "notes": "Raw tool-event capture from episodes.jsonl (PostToolUse/Stop hooks); deterministic, unreviewed"},
    {"canonical_id": "session-transcript", "family": "SESSION", "authority_level": "informational",
     "issuing_body": "harness", "notes": "Full session transcripts (~/.claude/projects/*/); distiller input, contains untrusted tool output"},
    {"canonical_id": "distilled-lesson", "family": "DISTILLER", "authority_level": "informational",
     "issuing_body": "session-end distiller (Opus via Claude CLI)", "notes": "Candidate lessons extracted from episode log + transcript; enter provisional until evidence-promoted"},
    {"canonical_id": "verified-outcome", "family": "OUTCOME", "authority_level": "guidance",
     "issuing_body": "environment", "notes": "Lesson backed by a verifiable outcome: test passed, exit 0 after documented failure, task verified"},
    {"canonical_id": "user-correction", "family": "USER", "authority_level": "organizational",
     "issuing_body": "user", "notes": "Explicit user correction or confirmed preference; outranks efficiency heuristics"},
    {"canonical_id": "memory-backfill", "family": "BACKFILL", "authority_level": "guidance",
     "issuing_body": "flat memory files", "notes": "One-time bootstrap from ~/.claude/.../memory/*.md; human-curated but of mixed recency"},
    {"canonical_id": "janitor-action", "family": "JANITOR", "authority_level": "informational",
     "issuing_body": "maintenance janitors", "notes": "Consolidation/supersession/scoping actions recorded as episodes so the curation layer can learn its own thresholds"},
]
