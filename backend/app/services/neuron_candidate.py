"""Shared neuron scoring vocabulary: the candidate shape and the freshness term.

Extracted from ``neuron_service`` by roadmap record durability-modular-monolith
(04, seam 2) to break the ``neuron_index`` <-> ``neuron_service`` import cycle.

Both of these are VOCABULARY, not behavior: a data shape and a SQL expression
that several layers must agree on. Leaving them inside the service meant
``neuron_index`` — which is infrastructure UNDER the service — had to import the
service back, and ``executor`` had to reach through the service's privacy for
``_FRESHNESS_SQL``. A leading underscore being imported across three modules was
the tell.

This module deliberately depends on nothing but the standard library, so anything
may import it without inheriting a graph.
"""

from dataclasses import dataclass


@dataclass
class NeuronCandidate:
    """Lightweight neuron representation for scoring (no content blob)."""
    id: int
    label: str
    summary: str | None
    department: str | None
    role_key: str | None
    avg_utility: float
    invocations: int
    created_at_query_count: int
    keyword_hits: int = 0
    # Cold-start prior inputs (authority + freshness + centrality)
    authority_level: str | None = None
    freshness_days: float | None = None
    centrality: float = 0.0

    @property
    def region(self) -> str | None:
        """Generic region vocabulary — silos are labeled regions."""
        return self.department


# SQL expression for days since the most authoritative provenance date.
# Public by name because three modules build queries from it; it was
# ``_FRESHNESS_SQL`` when it lived in neuron_service, and imported anyway.
FRESHNESS_SQL = (
    "EXTRACT(EPOCH FROM (now() - COALESCE(last_verified, "
    "effective_date::timestamp, created_at))) / 86400.0"
)
