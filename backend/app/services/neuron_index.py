"""NeuronIndex — a bounded, single-worker projection of scoring inputs.

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

The projection stores aggregate counters plus only the configured recent burst
window; it never retains all historical firing IDs. It is intentionally used
only in process-local coherence mode. Database mode reads canonical aggregates
directly so horizontally scaled workers cannot diverge.
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
        self._offsets: dict[int, list[int]] = {}    # id -> recent firing offsets for burst
        self._fire_counts: dict[int, int] = {}      # id -> all-time distinct query count
        self._last: dict[int, int] = {}             # id -> max offset
        self._recent_qids: dict[int, list[int]] = {}  # id -> bounded local dedup window
        self._dept_counts: dict[str, int] = {}       # department -> all-time distinct query count
        self._dept_recent_qids: dict[str, list[int]] = {}  # dept -> bounded local dedup window
        self._retention_queries = 0
        self._active_count = 0                      # active neurons at build (genesis scale input)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, meta_rows: list[dict], firing_rows: list[tuple]) -> None:
        """Compatibility builder for controlled tests and small callers.

        Production loading uses ``load_aggregates`` so historical query IDs are
        aggregated in PostgreSQL and never transferred into the worker.
        """
        qids: dict[int, set[int]] = {}
        dept_qids: dict[str, set[int]] = {}
        last: dict[int, int] = {}
        last_qid: dict[int, int] = {}
        offsets: dict[int, list[int]] = {}
        dept_last_qid: dict[str, int] = {}
        for nid, qid, off, dept in firing_rows:
            offsets.setdefault(nid, []).append(off)
            qids.setdefault(nid, set()).add(qid)
            if off >= last.get(nid, -1):
                last[nid] = off
                last_qid[nid] = qid
            if dept:
                dept_qids.setdefault(dept, set()).add(qid)
                dept_last_qid[dept] = max(qid, dept_last_qid.get(dept, qid))
        self.load_aggregates(
            meta_rows,
            [(nid, offs) for nid, offs in offsets.items()],
            [
                (nid, len(ids), last[nid], last_qid[nid])
                for nid, ids in qids.items()
            ],
            [
                (dept, len(ids), dept_last_qid[dept])
                for dept, ids in dept_qids.items()
            ],
            retention_queries=max(
                (off for offs in offsets.values() for off in offs),
                default=0,
            ) + 1,
        )

    def load_aggregates(
        self,
        meta_rows: list[dict],
        recent_offset_rows: list[tuple[int, list[int]]],
        fire_stat_rows: list[tuple[int, int, int, int]],
        dept_stat_rows: list[tuple[str, int, int]],
        retention_queries: int,
    ) -> None:
        """Build from bounded recent offsets and all-time scalar aggregates."""
        with self._lock:
            meta = {r["id"]: r for r in meta_rows}
            self._meta = meta
            self._offsets = {
                int(nid): [int(off) for off in offsets]
                for nid, offsets in recent_offset_rows
            }
            self._fire_counts = {
                int(nid): int(count)
                for nid, count, _last, _qid in fire_stat_rows
            }
            self._last = {
                int(nid): int(last)
                for nid, _count, last, _qid in fire_stat_rows
            }
            self._recent_qids = {
                int(nid): [int(qid)]
                for nid, _count, _last, qid in fire_stat_rows
                if qid is not None
            }
            self._dept_counts = {
                dept: int(count)
                for dept, count, _qid in dept_stat_rows
                if dept
            }
            self._dept_recent_qids = {
                dept: [int(qid)]
                for dept, _count, qid in dept_stat_rows
                if dept and qid is not None
            }
            self._retention_queries = max(0, int(retention_queries))
            self._active_count = sum(1 for m in meta.values() if m.get("is_active"))
            self._built_at = datetime.datetime.utcnow()
            self._loaded = True

    def invalidate(self) -> None:
        with self._lock:
            self._loaded = False
            self._meta, self._offsets, self._fire_counts = {}, {}, {}
            self._last, self._recent_qids = {}, {}
            self._dept_counts, self._dept_recent_qids = {}, {}
            self._retention_queries = 0
            self._active_count = 0

    def on_firing(self, neuron_id: int, query_id: int, offset: int) -> None:
        """Incremental update on a recorded firing — mirrors record_firing exactly."""
        with self._lock:
            if not self._loaded:
                return
            offsets = self._offsets.setdefault(neuron_id, [])
            offsets.append(offset)
            cutoff = offset - self._retention_queries
            if offsets and offsets[0] < cutoff:
                self._offsets[neuron_id] = [o for o in offsets if o >= cutoff]
            recent_qids = self._recent_qids.setdefault(neuron_id, [])
            if query_id not in recent_qids:
                self._fire_counts[neuron_id] = (
                    self._fire_counts.get(neuron_id, 0) + 1
                )
                recent_qids.append(query_id)
                del recent_qids[:-max(1, self._retention_queries)]
            if offset > self._last.get(neuron_id, -1):
                self._last[neuron_id] = offset
            m = self._meta.get(neuron_id)
            if m is not None:
                m["invocations"] = (m.get("invocations") or 0) + 1
                dept = m.get("department")
                if dept:
                    dept_qids = self._dept_recent_qids.setdefault(dept, [])
                    if query_id not in dept_qids:
                        self._dept_counts[dept] = (
                            self._dept_counts.get(dept, 0) + 1
                        )
                        dept_qids.append(query_id)
                        del dept_qids[:-max(1, self._retention_queries)]

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
                count = self._fire_counts.get(nid)
                if count:
                    fires[nid] = count
                    last[nid] = self._last[nid]
            return fires, last

    def dept_totals(self, departments: list[str]) -> dict[str, int]:
        with self._lock:
            return {
                dp: self._dept_counts[dp]
                for dp in departments if dp in self._dept_counts
            }

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
    from app.config import settings

    total_result = await db.execute(text(
        "SELECT COALESCE(total_queries, 0) FROM system_state WHERE id = 1"
    ))
    total_queries = int(total_result.scalar_one_or_none() or 0)
    recent_start = max(0, total_queries - settings.burst_window_queries)
    recent_result = await db.execute(text(
        "SELECT neuron_id, array_agg(global_query_offset ORDER BY global_query_offset) "
        "FROM neuron_firings WHERE global_query_offset >= :recent_start "
        "GROUP BY neuron_id"
    ), {"recent_start": recent_start})
    recent_rows = [
        (int(r[0]), [int(off) for off in r[1]])
        for r in recent_result.all()
    ]
    fire_result = await db.execute(text(
        "SELECT neuron_id, count(DISTINCT query_id), "
        "max(global_query_offset), max(query_id) "
        "FROM neuron_firings GROUP BY neuron_id"
    ))
    fire_rows = [
        (int(r[0]), int(r[1]), int(r[2]), int(r[3]) if r[3] is not None else None)
        for r in fire_result.all()
    ]
    dept_result = await db.execute(text(
        "SELECT n.department, count(DISTINCT f.query_id), max(f.query_id) "
        "FROM neuron_firings f JOIN neurons n ON n.id = f.neuron_id "
        "WHERE n.department IS NOT NULL GROUP BY n.department"
    ))
    dept_rows = [
        (r[0], int(r[1]), int(r[2]) if r[2] is not None else None)
        for r in dept_result.all()
    ]
    _index.load_aggregates(
        meta_rows,
        recent_rows,
        fire_rows,
        dept_rows,
        retention_queries=settings.burst_window_queries,
    )


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


def active_count() -> int:
    """Active-neuron count at last index build; 0 when the index is unloaded
    (callers must treat 0 as 'unknown' and fall back to strict thresholds)."""
    return _index._active_count if _index.is_loaded else 0
