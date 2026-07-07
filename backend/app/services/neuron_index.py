"""NeuronIndex — a materialized, in-memory projection of the canonical neuron graph.

The neuron graph (neurons + neuron_firings tables) is the source of truth; this
index is a *derived* view of the query-independent scoring inputs, so query-time
recall reads memory instead of firing several aggregate SQL queries per request.

Its accessors are equivalent to the live queries they replace:
  - burst_counts   == _fetch_burst_counts       (count of firing rows in the window)
  - fire_stats     == _fetch_neuron_fire_stats  (distinct query_id count + max offset)
  - dept_totals    == _fetch_dept_fire_totals   (per-dept distinct query_id count)
  - candidates     == _load_candidates_by_ids   (metadata + in-memory keyword_hits/freshness)

Distinctness is keyed on query_id (matching the DB), while the firing offset drives
the burst window and last-fire. Freshness is anchored to the DB's own computation at
build time plus elapsed wall-clock, so it tracks the DB frame without timezone drift.

Lifecycle: built lazily/at startup from the DB, updated incrementally on each firing
(so burst/recency/invocations stay exact), invalidated on neuron writes, and fully
rebuilt on the maintenance heartbeat. Feature-flagged via settings.neuron_index_enabled.
"""

import datetime
import threading


class _NeuronIndex:
    """Thread-safe materialization of per-neuron scoring inputs + firing history."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loaded = False
        self._built_at = datetime.datetime.utcnow()
        self._meta: dict[int, dict] = {}            # id -> metadata dict (incl. freshness_days @ build)
        self._offsets: dict[int, list[int]] = {}    # id -> firing offsets (multiplicity) for burst
        self._qids: dict[int, set[int]] = {}        # id -> distinct query_ids
        self._last: dict[int, int] = {}             # id -> max offset
        self._dept_qids: dict[str, set[int]] = {}   # department -> distinct query_ids

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, meta_rows: list[dict], firing_rows: list[tuple]) -> None:
        """Build the index. firing_rows: (neuron_id, query_id, offset, department)."""
        with self._lock:
            meta = {r["id"]: r for r in meta_rows}
            offsets: dict[int, list[int]] = {}
            qids: dict[int, set[int]] = {}
            last: dict[int, int] = {}
            dept_qids: dict[str, set[int]] = {}
            for nid, qid, off, dept in firing_rows:
                offsets.setdefault(nid, []).append(off)
                qids.setdefault(nid, set()).add(qid)
                if off > last.get(nid, -1):
                    last[nid] = off
                if dept:
                    dept_qids.setdefault(dept, set()).add(qid)
            self._meta, self._offsets, self._qids = meta, offsets, qids
            self._last, self._dept_qids = last, dept_qids
            self._built_at = datetime.datetime.utcnow()
            self._loaded = True

    def invalidate(self) -> None:
        with self._lock:
            self._loaded = False
            self._meta, self._offsets, self._qids = {}, {}, {}
            self._last, self._dept_qids = {}, {}

    def on_firing(self, neuron_id: int, query_id: int, offset: int) -> None:
        """Incremental update on a recorded firing — mirrors record_firing exactly."""
        with self._lock:
            if not self._loaded:
                return
            self._offsets.setdefault(neuron_id, []).append(offset)
            self._qids.setdefault(neuron_id, set()).add(query_id)
            if offset > self._last.get(neuron_id, -1):
                self._last[neuron_id] = offset
            m = self._meta.get(neuron_id)
            if m is not None:
                m["invocations"] = (m.get("invocations") or 0) + 1
                dept = m.get("department")
                if dept:
                    self._dept_qids.setdefault(dept, set()).add(query_id)

    # ---- accessors (reproduce the DB aggregate queries) ----

    def burst_counts(self, ids: list[int], window: int) -> dict[int, int]:
        with self._lock:
            out: dict[int, int] = {}
            for nid in ids:
                offs = self._offsets.get(nid)
                if not offs:
                    continue
                c = sum(1 for o in offs if o >= window)
                if c:
                    out[nid] = c
            return out

    def fire_stats(self, ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
        with self._lock:
            fires: dict[int, int] = {}
            last: dict[int, int] = {}
            for nid in ids:
                q = self._qids.get(nid)
                if q:
                    fires[nid] = len(q)
                    last[nid] = self._last[nid]
            return fires, last

    def dept_totals(self, departments: list[str]) -> dict[str, int]:
        with self._lock:
            return {dp: len(self._dept_qids[dp]) for dp in departments if dp in self._dept_qids}

    def candidates(self, ids: list[int], keywords: list[str]):
        """Reproduce _load_candidates_by_ids for the default (unrestricted) requester."""
        from app.services.neuron_service import NeuronCandidate
        kws = [k.lower() for k in keywords] if keywords else []
        with self._lock:
            elapsed_days = (datetime.datetime.utcnow() - self._built_at).total_seconds() / 86400.0
            out = []
            for nid in ids:
                m = self._meta.get(nid)
                if not m or not m.get("is_active", True):
                    continue
                hits = 0
                if kws:
                    lab = (m.get("label") or "").lower()
                    summ = (m.get("summary") or "").lower()
                    for k in kws:
                        hits += (1 if k in lab else 0) + (1 if k in summ else 0)
                fd = m.get("freshness_days")
                fresh = None if fd is None else max(0.0, float(fd) + elapsed_days)
                out.append(NeuronCandidate(
                    id=nid, label=m.get("label"), summary=m.get("summary"),
                    department=m.get("department"), role_key=m.get("role_key"),
                    avg_utility=m.get("avg_utility") or 0.5,  # match DB's `r[5] or 0.5` (0.0 -> 0.5)
                    invocations=m.get("invocations") or 0,
                    created_at_query_count=m.get("created_at_query_count") or 0,
                    keyword_hits=hits, authority_level=m.get("authority_level"),
                    freshness_days=fresh, centrality=m.get("centrality") or 0.0,
                ))
        # Deterministic order (matches _load_candidates_by_ids' ORDER BY id) so the
        # order-sensitive hybrid-RRF relevance is identical to the DB path.
        out.sort(key=lambda c: c.id)
        return out


# Module-level singleton
_index = _NeuronIndex()


async def ensure_index_loaded(db) -> None:
    """Build the index from the canonical graph if not already loaded."""
    if _index.is_loaded:
        return
    from sqlalchemy import text
    from app.services.neuron_service import _FRESHNESS_SQL
    meta_result = await db.execute(text(
        "SELECT id, label, summary, department, role_key, avg_utility, invocations, "
        f"created_at_query_count, authority_level, centrality, is_active, ({_FRESHNESS_SQL}) AS freshness_days "
        "FROM neurons"
    ))
    meta_rows = [
        {
            "id": r[0], "label": r[1], "summary": r[2], "department": r[3], "role_key": r[4],
            "avg_utility": r[5], "invocations": r[6], "created_at_query_count": r[7],
            "authority_level": r[8], "centrality": r[9], "is_active": r[10],
            "freshness_days": float(r[11]) if r[11] is not None else None,
        }
        for r in meta_result.all()
    ]
    fire_result = await db.execute(text(
        "SELECT f.neuron_id, f.query_id, f.global_query_offset, n.department "
        "FROM neuron_firings f JOIN neurons n ON n.id = f.neuron_id"
    ))
    firing_rows = [(int(r[0]), int(r[1]), int(r[2]), r[3]) for r in fire_result.all()]
    _index.load(meta_rows, firing_rows)


def invalidate_index() -> None:
    """Call after neuron writes (create/update/deactivate) to force a rebuild."""
    _index.invalidate()


def index_on_firing(neuron_id: int, query_id: int, offset: int) -> None:
    """Incremental firing hook (no-op if the index isn't loaded)."""
    _index.on_firing(neuron_id, query_id, offset)


def index_is_loaded() -> bool:
    return _index.is_loaded


def get_index() -> _NeuronIndex:
    return _index
