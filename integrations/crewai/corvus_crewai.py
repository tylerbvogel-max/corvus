"""Corvus memory provider for CrewAI — trusted, evidence-gated agent memory.

Corvus is a neuron-graph memory server (spreading activation + inhibitory
regulation over typed edges) with a write gate: every save is a staged,
audited proposal — observational writes auto-commit and decay if never
reinforced; authoritative writes queue for human review. No agent can
silently poison long-term memory. Superseded facts are invalidated with a
temporal change log (valid_from/valid_to), never deleted.

Two integration surfaces, both thin HTTP clients of a running Corvus
memory tenant (default http://localhost:8005):

1. ``CorvusStorageBackend`` — implements CrewAI's ``StorageBackend``
   protocol for ``Memory(storage=...)``. Saves route text through the
   Corvus write gate. NOTE the upstream protocol gap: ``search()``
   receives only CrewAI's query *embedding* (its own vector space), so a
   remote text-pipeline memory cannot serve protocol-level search today.
   This backend therefore raises on ``search()`` with a pointed message,
   and text recall is provided by the tools below. (Upstream fix we're
   proposing: pass the query text alongside its embedding.)

2. ``corvus_recall_tool`` / ``corvus_remember_tool`` — CrewAI ``@tool``
   functions agents call with text. Recall runs the full Corvus prepare
   pipeline (classify → prefilter → graph spread → inhibition → rank);
   remember goes through the evidence-gated write path.

Usage:
    from corvus_crewai import make_corvus_tools
    recall, remember = make_corvus_tools("http://localhost:8005")
    agent = Agent(role=..., tools=[recall, remember])
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from typing import Any

DEFAULT_BASE_URL = "http://localhost:8005"
DEFAULT_TIMEOUT = 30


def _post(base_url: str, path: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class CorvusStorageBackend:
    """CrewAI StorageBackend whose writes ride the Corvus write gate.

    Reads: see the module docstring — protocol search() is embedding-only,
    which cannot address a remote text-pipeline store; use the tools.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL,
                 scope: str = "User", timeout: int = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url
        self.scope = scope
        self.timeout = timeout

    def save(self, records: list[Any]) -> None:
        for record in records:
            content = getattr(record, "content", None) or str(record)
            source = (getattr(record, "metadata", None) or {}).get("source", "crewai")
            _post(self.base_url, "/remember", {
                "lesson": content[:8000],
                "evidence": f"CrewAI memory save via {source} "
                            f"at {datetime.utcnow().isoformat(timespec='seconds')}Z",
                "label": content[:120],
                "scope": self.scope,
            }, self.timeout)

    def search(self, query_embedding: list[float], scope_prefix: str | None = None,
               categories: list[str] | None = None,
               metadata_filter: dict[str, Any] | None = None,
               limit: int = 10, min_score: float = 0.0) -> list[tuple[Any, float]]:
        raise NotImplementedError(
            "CrewAI's StorageBackend.search() passes only the query embedding "
            "(from CrewAI's own embedder), which a remote graph-memory pipeline "
            "cannot use — Corvus retrieval is text-driven (classify → graph "
            "spread → inhibition). Use make_corvus_tools() for recall, and see "
            "the README for the upstream protocol change this needs."
        )

    def delete(self, **kwargs: Any) -> int:
        # Corvus never hard-deletes memories: supersession-with-history is
        # the deletion model (temporal change log keeps the record).
        return 0

    def update(self, record: Any) -> None:
        self.save([record])

    def get_record(self, record_id: str) -> Any | None:
        return None

    def list_records(self, scope_prefix: str | None = None,
                     limit: int = 200, **kwargs: Any) -> list[Any]:
        return []


def corvus_recall(query: str, base_url: str = DEFAULT_BASE_URL, top_k: int = 5) -> str:
    """Text recall through the full Corvus prepare pipeline."""
    data = _post(base_url, "/recall",
                 {"query": query, "top_k": top_k, "source": "crewai",
                  "include_content": True})
    hits = data.get("hits", [])
    if not hits:
        return "No memories found."
    lines = []
    for h in hits:
        lines.append(f"- [{h.get('score', 0)}] {h.get('label')}: "
                     f"{(h.get('content') or h.get('summary') or '').strip()[:300]}")
    return "\n".join(lines)


def corvus_remember(fact: str, evidence: str,
                    base_url: str = DEFAULT_BASE_URL, scope: str = "User") -> str:
    """Evidence-gated save. Facts without evidence do not enter the graph."""
    data = _post(base_url, "/remember", {
        "lesson": fact[:8000], "evidence": evidence[:4000],
        "label": fact[:120], "scope": scope,
    })
    route = data.get("route", "?")
    return (f"Saved (route={route}): "
            + ("committed with audit trail; unreinforced memories decay."
               if route == "auto" else
               "queued for human review (authoritative-tier write)."))


def make_corvus_tools(base_url: str = DEFAULT_BASE_URL):
    """Build (recall_tool, remember_tool) as CrewAI tools.

    Imported lazily so this module works without crewai installed
    (e.g. for the backend-only integration path).
    """
    from crewai.tools import tool

    @tool("corvus_recall")
    def recall_tool(query: str) -> str:
        """Recall relevant long-term memories for a query. Retrieval runs a
        neuron-graph pipeline (semantic + spreading activation), not flat
        vector search — related facts one hop away are surfaced too."""
        return corvus_recall(query, base_url)

    @tool("corvus_remember")
    def remember_tool(fact: str, evidence: str) -> str:
        """Save a fact to trusted long-term memory. `evidence` must state
        what observably backs the fact — evidence-free writes are the main
        memory-poisoning vector and are rejected by the write gate."""
        return corvus_remember(fact, evidence, base_url)

    return recall_tool, remember_tool
