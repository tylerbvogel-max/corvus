"""Concept neuron definitions for the agentic-memory domain."""

CONCEPT_DEFINITIONS: list[dict] = [
    {
        "label": "Evidence-Gated Memory",
        "summary": "Nothing enters the graph as truth without evidence: outcomes, exit codes, test results, or explicit user confirmation gate admission and promotion",
        "content": (
            "Bad memory is worse than no memory — a confidently wrong lesson repeats forever. "
            "Every candidate memory therefore carries evidence links at birth (the episodes "
            "that produced it) and enters at informational authority. Promotion requires a "
            "verifiable outcome: a test that passed, an exit 0 after a documented failure, a "
            "task the user verified, or an explicit user confirmation. Unreinforced candidates "
            "are reclaimed by consolidation decay rather than accumulating as noise. The "
            "distiller extracts candidates asynchronously; nothing writes to the graph "
            "directly from a live session."
        ),
        "direct_patterns": ["%evidence%", "%gate%"],
        "content_patterns": ["%evidence%", "%verified%", "%outcome%", "%confirm%", "%provisional%"],
    },
    {
        "label": "Supersession with History",
        "summary": "When the world changes, old memories are demoted with a was-true-until record, not deleted — 'this used to be different' is itself useful context",
        "content": (
            "Agentic facts rot: tools update, repos refactor, services move ports. When a "
            "newer memory contradicts an older one because the world changed (not because "
            "someone was wrong), the resolution is supersession with history: the old memory "
            "is demoted and linked from its successor, retaining when it stopped being true. "
            "This differs from a disagreement, where two sources describe the same world and "
            "one is simply incorrect. Staleness checks compare evidence recency before "
            "assuming anyone erred."
        ),
        "direct_patterns": ["%supersed%", "%stale%"],
        "content_patterns": ["%supersed%", "%stale%", "%outdated%", "%was true%", "%no longer%", "%changed%"],
    },
    {
        "label": "Context Scoping",
        "summary": "Contextual truths — true in repo A, false in repo B — are conditioned on their scope rather than merged, deleted, or promoted to global",
        "content": (
            "Many lessons are true only within a context: a migration workaround applies to "
            "one project's alembic chain; a port convention applies to one machine; a git "
            "policy applies to one remote. When two memories disagree across contexts, the "
            "correct verdict is often neither merge nor supersession but SCOPING: push the "
            "claim down from global to conditioned-on-context. Scope boundaries are also "
            "policy boundaries — memories must not cross project or tenant walls that the "
            "user has declared closed."
        ),
        "direct_patterns": ["%scope%", "%scoped%"],
        "content_patterns": ["%scope%", "%per-project%", "%this repo%", "%this machine%", "%context%", "%boundary%"],
    },
    {
        "label": "Episodic-to-Semantic Consolidation",
        "summary": "Near-duplicate lessons across independent sessions are confirmations, not noise: fuse them into one high-weight lesson, keep the episodes as provenance",
        "content": (
            "Raw episodes are cheap and abundant; durable knowledge is what survives "
            "repetition with consistent outcomes. When the same lesson emerges from N "
            "independent sessions, that is N confirmations — fuse the duplicates into a "
            "single consolidated lesson, accumulate the weight, and keep every source "
            "episode linked as provenance. Critically, independence is required: an episode "
            "from a session where the lesson was already injected into context is a usage, "
            "not a confirmation, and must not inflate consolidation counts (the "
            "self-reinforcement trap)."
        ),
        "direct_patterns": ["%consolidat%"],
        "content_patterns": ["%consolidat%", "%duplicate%", "%confirmation%", "%fuse%", "%near-dup%"],
    },
    {
        "label": "Ambient Recall",
        "summary": "Memory is injected into sessions by hooks — synchronous, free, zero model cooperation — while writes stay asynchronous and expensive",
        "content": (
            "The read path is felt on every agent step, so it must be cheap: embed-only "
            "recall (~150ms, no LLM) injected at session start and per-prompt as background "
            "context. The write path is felt never, so it can be expensive: deterministic "
            "event capture, then asynchronous distillation and review. Injected memories are "
            "declarative facts with provenance, framed as background context and never as "
            "instructions; injection events are themselves logged so later attribution and "
            "consolidation can distinguish influence from independent rediscovery."
        ),
        "direct_patterns": ["%recall%", "%inject%"],
        "content_patterns": ["%recall%", "%inject%", "%hot path%", "%ambient%", "%session start%"],
    },
]
