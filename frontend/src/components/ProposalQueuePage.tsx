import { useState, useEffect, useCallback, useRef, useMemo, type ReactNode } from 'react';
import {
  fetchProposals,
  fetchDedupClusters,
  fetchProposalDetail,
  reviewProposal,
  applyProposal,
  fetchProposalStats,
  fetchWhoami,
  type ProposalSummary,
  type ProposalDetail,
  type ProposalStats,
  type ProposalItem,
  type GapEvidence,
  type DocumentEvidence,
  type Whoami,
  type RenderedFusionPlan,
  type RenderedFusionMember,
  type RenderedFieldReceipt,
} from '../api';
import { getReviewerName, setReviewerName } from '../auth';
import { useListKeyboardNav } from '../hooks/useListKeyboardNav';
import { diffWords } from 'diff';

type StateFilter = 'all' | 'proposed' | 'approved' | 'rejected' | 'applied' | 'superseded';
export type OriginFilter = 'all' | 'autopilot' | 'integrity' | 'document' | 'emergent' | 'auditor' | 'manual';

const STATE_COLORS: Record<string, string> = {
  proposed: '#e8a838',
  approved: '#4caf50',
  rejected: '#e74c3c',
  applied: '#2196f3',
  superseded: '#7f8c8d', // terminal: old-state drifted before review/apply
};

const ORIGIN_COLORS: Record<string, string> = {
  autopilot: '#9b59b6',
  integrity: '#2196f3',
  document: '#4caf50',
  emergent: '#e8a838',
  auditor: '#e74c8b', // reconsolidation quality audit (scheduled doubt)
  manual: '#7f8c8d',
};

const SOURCE_OPTIONS: { key: OriginFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'autopilot', label: 'Autopilot (historical)' },
  { key: 'integrity', label: 'Integrity' },
  { key: 'document', label: 'Document' },
  { key: 'emergent', label: 'Emergent' },
  { key: 'auditor', label: 'Auditor' },
  { key: 'manual', label: 'Manual' },
];

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
  borderRadius: 4, padding: '4px 8px', fontSize: '0.8rem',
};

const inputStyle: React.CSSProperties = {
  ...selectStyle, width: '100%', boxSizing: 'border-box' as const,
};

// Structural grouping key — proposals with the same (origin, gap_source)
// typically have the same structural fix (e.g. "missing provenance" on 12
// neurons). Used as the FALLBACK when a proposal is not in a semantic
// near-duplicate cluster (server-side embedding cosine, /dedup-clusters).
function groupKey(p: ProposalSummary): string {
  return `${p.origin}::${p.gap_source || 'directive'}`;
}

function groupLabel(p: ProposalSummary): string {
  return `${p.origin} · ${p.gap_source || 'directive'}`;
}

export interface ProposalProducerTarget {
  origin: string;
  autopilot_run_id?: number | null;
  finding_id?: number | null;
  scan_id?: number | null;
  gap_source?: string | null;
}

interface ProposalQueuePageProps {
  initialOriginFilter?: OriginFilter;
  onNavigateToProducer?: (target: ProposalProducerTarget) => void;
}

export default function ProposalQueuePage({
  initialOriginFilter,
  onNavigateToProducer,
}: ProposalQueuePageProps = {}) {
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const [stats, setStats] = useState<ProposalStats | null>(null);
  const [selected, setSelected] = useState<ProposalDetail | null>(null);
  const [filter, setFilter] = useState<StateFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<OriginFilter>(initialOriginFilter ?? 'all');

  useEffect(() => {
    if (initialOriginFilter && initialOriginFilter !== sourceFilter) {
      setSourceFilter(initialOriginFilter);
    }
    // Intentionally depend only on initialOriginFilter — a parent that
    // passes a new value means "pre-seed to this filter now". Including
    // sourceFilter would clobber the user's manual filter changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialOriginFilter]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState(() => getReviewerName());
  const [whoami, setWhoami] = useState<Whoami | null>(null);
  const [reviewNotes, setReviewNotes] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);

  // New state — batch UX
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [groupByCluster, setGroupByCluster] = useState(false);
  // proposalId -> semantic-cluster index + each cluster's representative text.
  // null until fetched; fetch failure degrades to structural grouping only.
  const [semClusters, setSemClusters] = useState<{ byId: Map<number, number>; reps: string[] } | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [diffItem, setDiffItem] = useState<ProposalItem | null>(null);
  const [showCheatsheet, setShowCheatsheet] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<string | null>(null);

  const reviewerInputRef = useRef<HTMLInputElement | null>(null);
  const filterSelectRef = useRef<HTMLSelectElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const originParam = sourceFilter === 'all' ? undefined : sourceFilter;
      const [p, s] = await Promise.all([
        fetchProposals(filter === 'all' ? undefined : filter, undefined, originParam),
        fetchProposalStats(),
      ]);
      setProposals(p);
      setStats(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [filter, sourceFilter]);

  useEffect(() => { load(); }, [load]);

  // Fetch semantic near-duplicate clusters the first time grouping turns on.
  useEffect(() => {
    if (!groupByCluster || semClusters) return;
    fetchDedupClusters('proposed')
      .then(out => {
        const byId = new Map<number, number>();
        out.clusters.forEach((c, i) => c.proposal_ids.forEach(id => byId.set(id, i)));
        setSemClusters({ byId, reps: out.clusters.map(c => c.representative) });
      })
      .catch(() => setSemClusters({ byId: new Map(), reps: [] }));
  }, [groupByCluster, semClusters]);

  // Persist reviewer name so the X-Corvus-User header is sent on every
  // authed request — the server uses this as authoritative reviewed_by /
  // applied_by in RBAC disabled/header modes.
  useEffect(() => { setReviewerName(reviewer); }, [reviewer]);

  // Re-fetch whoami whenever reviewer changes so the banner reflects
  // exactly what the server will record.
  useEffect(() => {
    let cancel = false;
    fetchWhoami().then(w => { if (!cancel) setWhoami(w); }).catch(() => {});
    return () => { cancel = true; };
  }, [reviewer]);

  const selectProposal = useCallback(async (id: number) => {
    try {
      const detail = await fetchProposalDetail(id);
      setSelected(detail);
      setReviewNotes('');
    } catch (e) {
      setError(String(e));
    }
  }, []);

  // ONE-step lifecycle (reconsolidation kernel Phase 4): the backend
  // applies on approve inside the same transaction, so there is no
  // second /apply call. A 409 means the proposal's recorded old-state
  // drifted and it was terminally superseded instead.
  const handleReview = useCallback(async (action: 'approve' | 'reject') => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      const updated = await reviewProposal(selected.id, action, reviewer.trim(), reviewNotes);
      setSelected(updated);
      await load();
    } catch (e) {
      setError(String(e));
      await load(); // a 409 supersession still changed the row — refresh
    } finally {
      setActionLoading(false);
    }
  }, [selected, reviewer, reviewNotes, load]);

  const handleApply = useCallback(async () => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      const updated = await applyProposal(selected.id, reviewer.trim());
      setSelected(updated);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  }, [selected, reviewer, load]);

  // Bulk review — loops single-review endpoint sequentially so we surface
  // per-item failures without a half-applied batch.
  const handleBulk = useCallback(async (action: 'approve' | 'reject') => {
    if (selectedIds.size === 0 || !reviewer.trim()) return;
    const ids = Array.from(selectedIds);
    setActionLoading(true);
    let done = 0;
    const failures: number[] = [];
    const verb = action === 'approve' ? 'Approving' : 'Rejecting';
    const pastVerb = action === 'approve' ? 'approved' : 'rejected';
    for (const id of ids) {
      setBulkProgress(`${verb} ${done + 1}/${ids.length}...`);
      try {
        await reviewProposal(id, action, reviewer.trim(), reviewNotes);
      } catch {
        failures.push(id);
      }
      done += 1;
    }
    setBulkProgress(failures.length
      ? `Done. ${failures.length} failed: #${failures.join(', #')}`
      : `Done. ${done} ${pastVerb}.`);
    setSelectedIds(new Set());
    await load();
    setActionLoading(false);
    setTimeout(() => setBulkProgress(null), 4000);
  }, [selectedIds, reviewer, reviewNotes, load]);

  const toggleSelectId = useCallback((id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleGroupSelect = useCallback((key: string, ids: number[]) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      const allSelected = ids.every(id => next.has(id));
      if (allSelected) ids.forEach(id => next.delete(id));
      else ids.forEach(id => next.add(id));
      return next;
    });
    // key consumed as identity; nothing to persist
    void key;
  }, []);

  const toggleGroupCollapsed = useCallback((key: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  // Grouped view for cluster-approve ergonomics: semantic near-duplicate
  // clusters first (embedding cosine, server-side), structural key fallback.
  const grouped = useMemo(() => {
    const m = new Map<string, ProposalSummary[]>();
    for (const p of proposals) {
      const sem = semClusters?.byId.get(p.id);
      const k = sem !== undefined ? `sem::${sem}` : groupKey(p);
      const arr = m.get(k);
      if (arr) arr.push(p);
      else m.set(k, [p]);
    }
    return Array.from(m.entries());
  }, [proposals, semClusters]);

  // Keyboard shortcuts — scoped to the page, ignored when typing.
  useEffect(() => {
    const isTypingTarget = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      if (diffItem && e.key !== 'Escape') return;

      if (e.key === 'Escape') {
        if (diffItem) { setDiffItem(null); return; }
        if (showCheatsheet) { setShowCheatsheet(false); return; }
        return;
      }
      if (e.key === '?') { e.preventDefault(); setShowCheatsheet(s => !s); return; }
      if (e.key === '/') { e.preventDefault(); filterSelectRef.current?.focus(); return; }

      if (e.key === 'd') {
        if (selected?.items?.length) {
          setDiffItem(selected.items[0]);
          e.preventDefault();
        }
        return;
      }

      // j/k navigation is handled by useListKeyboardNav below.

      if (e.key === 'x' && selected) {
        e.preventDefault();
        toggleSelectId(selected.id);
        return;
      }

      if ((e.key === 'a' || e.key === 'A') && selected && selected.state === 'proposed') {
        e.preventDefault();
        if (!reviewer.trim()) { reviewerInputRef.current?.focus(); return; }
        void handleReview('approve'); // one-step: approve applies
        return;
      }
      if (e.key === 'r' && selected && selected.state === 'proposed') {
        e.preventDefault();
        if (!reviewer.trim()) { reviewerInputRef.current?.focus(); return; }
        void handleReview('reject');
        return;
      }
      if (e.key === 'y' && selected && selected.state === 'approved') {
        e.preventDefault();
        if (!reviewer.trim()) { reviewerInputRef.current?.focus(); return; }
        void handleApply();
        return;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, reviewer, showCheatsheet, diffItem, handleReview, handleApply, toggleSelectId]);

  useListKeyboardNav({
    items: proposals,
    selectedId: selected?.id ?? null,
    onSelect: (id) => { void selectProposal(id as number); },
    enabled: !diffItem && !showCheatsheet,
  });

  const pendingCount = stats?.proposed ?? 0;

  return (
    <div className="proposal-queue" style={{ display: 'flex', gap: 0, height: '100%', minHeight: 0 }}>
      {/* Left Panel — Collapsible Queue */}
      <div style={{
        width: queueCollapsed ? 40 : 360, flexShrink: 0,
        display: 'flex', flexDirection: 'column',
        borderRight: '1px solid var(--border)',
        transition: 'width 0.2s ease',
        overflow: 'hidden',
      }}>
        <button
          onClick={() => setQueueCollapsed(!queueCollapsed)}
          style={{
            background: 'none', border: 'none', color: 'var(--text)',
            cursor: 'pointer', padding: '8px 10px', fontSize: '0.85rem',
            display: 'flex', alignItems: 'center', gap: 6,
            borderBottom: '1px solid var(--border)',
          }}
        >
          <span style={{ fontSize: '0.7rem' }}>{queueCollapsed ? '\u25B6' : '\u25C0'}</span>
          {!queueCollapsed && (
            <span style={{ fontWeight: 600 }}>
              Queue{pendingCount > 0 ? ` (${pendingCount} pending)` : ''}
            </span>
          )}
          {!queueCollapsed && (
            <span
              onClick={(ev) => { ev.stopPropagation(); setShowCheatsheet(true); }}
              title="Keyboard shortcuts (?)"
              style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-dim)', padding: '0 4px' }}
            >?</span>
          )}
        </button>

        {!queueCollapsed && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 8px', flex: 1, overflow: 'hidden' }}>
            {stats && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: '0.7rem' }}>
                {/* ONE-step lifecycle: approve = apply, so 'approved' is not a
                    resting population — surface it only as an anomaly. */}
                {(['proposed', 'rejected', 'applied', 'superseded'] as const).map(s => (
                  <span key={s} style={{
                    padding: '1px 6px', borderRadius: 10,
                    background: STATE_COLORS[s] + '22', color: STATE_COLORS[s],
                    fontWeight: 600,
                  }}>
                    {s}: {stats[s]}
                  </span>
                ))}
                {stats.approved > 0 && (
                  <span
                    title="Approved-but-unapplied rows cannot occur under the one-step lifecycle — these are stuck legacy rows; run the stale-approved sweep to retire them."
                    style={{
                      padding: '1px 6px', borderRadius: 10,
                      background: '#e74c3c22', color: '#e74c3c',
                      fontWeight: 600, border: '1px dashed #e74c3c',
                    }}
                  >
                    ⚠ stuck approved: {stats.approved}
                  </span>
                )}
              </div>
            )}

            <div style={{
              display: 'flex', borderRadius: 8, overflow: 'hidden',
              border: '1px solid var(--border)', background: 'var(--bg-input)',
            }}>
              {SOURCE_OPTIONS.map(opt => (
                <button
                  key={opt.key}
                  onClick={() => setSourceFilter(opt.key)}
                  style={{
                    flex: 1, padding: '5px 4px', border: 'none', cursor: 'pointer',
                    fontSize: '0.68rem', fontWeight: 600,
                    background: sourceFilter === opt.key ? 'var(--accent)' : 'transparent',
                    color: sourceFilter === opt.key ? '#fff' : 'var(--text-dim)',
                    transition: 'background 0.15s, color 0.15s',
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 6 }}>
              <select
                ref={filterSelectRef}
                value={filter}
                onChange={e => setFilter(e.target.value as StateFilter)}
                style={{ ...selectStyle, flex: 1 }}
              >
                <option value="all">All States</option>
                <option value="proposed">Proposed</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="applied">Applied</option>
                <option value="superseded">Superseded</option>
              </select>
              <button
                onClick={() => setGroupByCluster(g => !g)}
                title="Group by origin · gap_source"
                style={{
                  ...selectStyle, cursor: 'pointer',
                  background: groupByCluster ? 'var(--accent)' : 'var(--bg-input)',
                  color: groupByCluster ? '#fff' : 'var(--text)',
                  fontWeight: 600,
                }}
              >
                Group
              </button>
            </div>

            {/* Bulk action bar */}
            {selectedIds.size > 0 && (
              <div style={{
                display: 'flex', flexDirection: 'column', gap: 4, padding: 6,
                border: '1px solid var(--accent)', borderRadius: 6,
                background: 'color-mix(in srgb, var(--accent) 10%, transparent)',
              }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 600 }}>
                  {selectedIds.size} selected
                </div>
                <input
                  type="text"
                  placeholder="Reviewer name (required)"
                  value={reviewer}
                  onChange={e => setReviewer(e.target.value)}
                  style={{ ...inputStyle, fontSize: '0.72rem', padding: '3px 6px' }}
                />
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    onClick={() => handleBulk('approve')}
                    disabled={actionLoading || !reviewer.trim()}
                    style={{
                      flex: 1, padding: '4px 6px', borderRadius: 4, border: 'none',
                      background: '#4caf50', color: '#fff', cursor: 'pointer',
                      fontSize: '0.72rem', fontWeight: 600,
                      opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                    }}
                  >Approve & apply all</button>
                  <button
                    onClick={() => handleBulk('reject')}
                    disabled={actionLoading || !reviewer.trim()}
                    style={{
                      flex: 1, padding: '4px 6px', borderRadius: 4, border: 'none',
                      background: '#e74c3c', color: '#fff', cursor: 'pointer',
                      fontSize: '0.72rem', fontWeight: 600,
                      opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                    }}
                  >Reject all</button>
                  <button
                    onClick={() => setSelectedIds(new Set())}
                    style={{
                      padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)',
                      background: 'transparent', color: 'var(--text-dim)', cursor: 'pointer',
                      fontSize: '0.72rem',
                    }}
                  >✕</button>
                </div>
                {!reviewer.trim() && (
                  <div style={{ fontSize: '0.65rem', color: '#e8a838' }}>
                    Enter reviewer name above to enable
                  </div>
                )}
                {whoami && reviewer.trim() && (
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-dim)' }}>
                    Server will record as <strong style={{ color: 'var(--text)' }}>{whoami.user_id}</strong>
                    {whoami.source !== 'disabled' && <> · {whoami.source}</>}
                  </div>
                )}
              </div>
            )}
            {bulkProgress && (
              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', padding: '2px 4px' }}>
                {bulkProgress}
              </div>
            )}

            <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {loading && <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>Loading...</div>}
              {error && <div style={{ color: '#e74c3c', padding: 8, fontSize: '0.8rem' }}>{error}</div>}
              {!loading && proposals.length === 0 && (
                <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>No proposals found.</div>
              )}

              {!groupByCluster && proposals.map(p => (
                <ProposalRow
                  key={p.id}
                  p={p}
                  isFocused={selected?.id === p.id}
                  isChecked={selectedIds.has(p.id)}
                  onOpen={() => selectProposal(p.id)}
                  onCheck={() => toggleSelectId(p.id)}
                  onProducerClick={onNavigateToProducer ? (row) => onNavigateToProducer({
                    origin: row.origin,
                    autopilot_run_id: row.autopilot_run_id,
                    finding_id: row.finding_id,
                    scan_id: row.scan_id,
                    gap_source: row.gap_source,
                  }) : undefined}
                />
              ))}

              {groupByCluster && grouped.map(([key, rows]) => {
                const ids = rows.map(r => r.id);
                const allChecked = ids.every(id => selectedIds.has(id));
                const someChecked = !allChecked && ids.some(id => selectedIds.has(id));
                const collapsed = collapsedGroups.has(key);
                return (
                  <div key={key} style={{ border: '1px solid var(--border)', borderRadius: 6 }}>
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '6px 8px', background: 'var(--bg-card)',
                      cursor: 'pointer', borderRadius: 6,
                    }}>
                      <input
                        type="checkbox"
                        checked={allChecked}
                        ref={el => { if (el) el.indeterminate = someChecked; }}
                        onChange={() => toggleGroupSelect(key, ids)}
                        onClick={e => e.stopPropagation()}
                      />
                      <span
                        onClick={() => toggleGroupCollapsed(key)}
                        style={{ flex: 1, fontSize: '0.75rem', fontWeight: 600 }}
                      >
                        {collapsed ? '▸' : '▾'} {key.startsWith('sem::')
                          ? `≈ ${semClusters?.reps[Number(key.slice(5))] || 'near-duplicates'}`
                          : groupLabel(rows[0])} <span style={{ color: 'var(--text-dim)', fontWeight: 400 }}>· {rows.length}</span>
                      </span>
                    </div>
                    {!collapsed && (
                      <div style={{ padding: '4px 6px 6px 18px', display: 'flex', flexDirection: 'column', gap: 3 }}>
                        {rows.map(p => (
                          <ProposalRow
                            key={p.id}
                            p={p}
                            isFocused={selected?.id === p.id}
                            isChecked={selectedIds.has(p.id)}
                            onOpen={() => selectProposal(p.id)}
                            onCheck={() => toggleSelectId(p.id)}
                            onProducerClick={onNavigateToProducer ? (row) => onNavigateToProducer({
                              origin: row.origin,
                              autopilot_run_id: row.autopilot_run_id,
                              finding_id: row.finding_id,
                              scan_id: row.scan_id,
                              gap_source: row.gap_source,
                            }) : undefined}
                            compact
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Right Panel — Detail + Actions */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
        {!selected ? (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', paddingTop: 40 }}>
            Select a proposal to view details. Press <kbd>?</kbd> for keyboard shortcuts.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 900 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0 }}>Proposal #{selected.id}</h3>
              <span style={{
                padding: '3px 10px', borderRadius: 10, fontSize: '0.8rem',
                background: STATE_COLORS[selected.state] + '22', color: STATE_COLORS[selected.state],
                fontWeight: 600,
              }}>
                {selected.state}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: '0.8rem', color: 'var(--text-dim)' }}>
              <span>Model: {selected.llm_model || '?'}</span>
              <span>Eval: {selected.eval_overall}/5</span>
              <span>Priority: {selected.priority_score.toFixed(3)}</span>
              <span>Query: #{selected.query_id}</span>
              {selected.prompt_hash && <span title={selected.prompt_hash}>Hash: {selected.prompt_hash.slice(0, 8)}...</span>}
            </div>

            <Section title="Gap Evidence">
              <div style={{ fontSize: '0.8rem', marginBottom: 8 }}>
                <strong>Source:</strong> {selected.gap_source || 'directive'}
              </div>
              {selected.gap_description && (
                <div style={{ fontSize: '0.8rem', marginBottom: 8, color: 'var(--text-dim)' }}>
                  {selected.gap_description}
                </div>
              )}
              {selected.gap_evidence.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {selected.gap_evidence.map((ev, i) => (
                    <EvidenceCard key={i} evidence={ev} />
                  ))}
                </div>
              )}
            </Section>

            {selected.llm_reasoning && (
              <Section title="LLM Reasoning">
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem', margin: 0, color: 'var(--text)' }}>
                  {selected.llm_reasoning}
                </pre>
              </Section>
            )}

            {selected.eval_text && (
              <Section title="Eval Verdict">
                <div style={{ fontSize: '0.8rem' }}>
                  <strong>Overall: {selected.eval_overall}/5</strong>
                </div>
                <div style={{ fontSize: '0.8rem', marginTop: 4, color: 'var(--text-dim)' }}>
                  {selected.eval_text}
                </div>
              </Section>
            )}

            <Section title={`Proposed Changes (${selected.items.length})`}>
              {selected.items.map(item => (
                <ItemCard key={item.id} item={item} proposalState={selected.state} onOpenDiff={() => setDiffItem(item)} />
              ))}
            </Section>

            {selected.reviewed_by && (
              <Section title="Review">
                <div style={{ fontSize: '0.8rem' }}>
                  <strong>{selected.state === 'rejected' ? 'Rejected' : 'Approved'}</strong> by {selected.reviewed_by}
                  {selected.reviewed_at && <> on {new Date(selected.reviewed_at).toLocaleString()}</>}
                </div>
                {selected.review_notes && (
                  <div style={{ fontSize: '0.8rem', marginTop: 4, color: 'var(--text-dim)' }}>
                    {selected.review_notes}
                  </div>
                )}
              </Section>
            )}

            {selected.applied_by && (
              <Section title="Application">
                <div style={{ fontSize: '0.8rem' }}>
                  Applied by {selected.applied_by}
                  {selected.applied_at && <> on {new Date(selected.applied_at).toLocaleString()}</>}
                </div>
              </Section>
            )}

            {selected.state === 'proposed' && (
              <Section title="Review Decision">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input
                    ref={reviewerInputRef}
                    type="text"
                    placeholder="Your name (required)"
                    value={reviewer}
                    onChange={e => setReviewer(e.target.value)}
                    style={inputStyle}
                  />
                  <textarea
                    placeholder="Review notes (optional)"
                    value={reviewNotes}
                    onChange={e => setReviewNotes(e.target.value)}
                    style={{ ...inputStyle, minHeight: 60, resize: 'vertical' }}
                  />
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      onClick={() => handleReview('approve')}
                      disabled={actionLoading || !reviewer.trim()}
                      title="Approve = apply, one transaction (kernel one-step lifecycle)"
                      style={{
                        flex: 1, padding: '8px 16px', borderRadius: 6, border: 'none',
                        background: '#4caf50', color: '#fff', cursor: 'pointer',
                        fontWeight: 600,
                        opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                      }}
                    >
                      Approve & Apply <kbd style={kbdInline}>a</kbd>
                    </button>
                    <button
                      onClick={() => handleReview('reject')}
                      disabled={actionLoading || !reviewer.trim()}
                      style={{
                        flex: 1, padding: '8px 16px', borderRadius: 6, border: 'none',
                        background: '#e74c3c', color: '#fff', cursor: 'pointer',
                        fontWeight: 600,
                        opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                      }}
                    >
                      Reject <kbd style={kbdInline}>r</kbd>
                    </button>
                  </div>
                </div>
              </Section>
            )}

            {selected.state === 'approved' && (
              <Section title="Apply to Graph">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input
                    ref={reviewerInputRef}
                    type="text"
                    placeholder="Your name (required)"
                    value={reviewer}
                    onChange={e => setReviewer(e.target.value)}
                    style={inputStyle}
                  />
                  <button
                    onClick={handleApply}
                    disabled={actionLoading || !reviewer.trim()}
                    style={{
                      padding: '8px 16px', borderRadius: 6, border: 'none',
                      background: '#2196f3', color: '#fff', cursor: 'pointer',
                      fontWeight: 600,
                      opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                    }}
                  >
                    Apply to Graph <kbd style={kbdInline}>y</kbd>
                  </button>
                </div>
              </Section>
            )}
          </div>
        )}
      </div>

      {diffItem && <DiffModal item={diffItem} onClose={() => setDiffItem(null)} />}
      {showCheatsheet && <Cheatsheet onClose={() => setShowCheatsheet(false)} />}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

const kbdInline: React.CSSProperties = {
  marginLeft: 6, padding: '0 5px', borderRadius: 3,
  background: 'rgba(0,0,0,0.25)', fontSize: '0.7rem',
  fontFamily: 'monospace', color: '#fff', opacity: 0.8,
};

function producerLabel(p: ProposalSummary): string | null {
  if (p.origin === 'autopilot' && p.autopilot_run_id) return `run #${p.autopilot_run_id}`;
  if (p.origin === 'integrity' && p.finding_id) return `finding #${p.finding_id}`;
  if (p.origin === 'document') return 'Document ingest';
  if (p.origin === 'emergent') return 'Emergent queue';
  if (p.origin === 'auditor') return 'Quality audit';
  return null;
}

function ProposalRow({
  p, isFocused, isChecked, onOpen, onCheck, compact, onProducerClick,
}: {
  p: ProposalSummary;
  isFocused: boolean;
  isChecked: boolean;
  onOpen: () => void;
  onCheck: () => void;
  compact?: boolean;
  onProducerClick?: (p: ProposalSummary) => void;
}) {
  const prodLabel = producerLabel(p);
  return (
    <div
      style={{
        display: 'flex', gap: 6, alignItems: 'flex-start',
        padding: compact ? '4px 6px' : '6px 8px', borderRadius: 6, cursor: 'pointer',
        background: isFocused ? 'var(--bg-active)' : 'var(--bg-card)',
        border: `1px solid ${isFocused ? 'var(--accent)' : 'var(--border)'}`,
      }}
    >
      <input
        type="checkbox"
        checked={isChecked}
        onChange={onCheck}
        onClick={e => e.stopPropagation()}
        style={{ marginTop: 2 }}
      />
      <div style={{ flex: 1 }} onClick={onOpen}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
          <span style={{ fontWeight: 600, fontSize: '0.8rem' }}>#{p.id}</span>
          <span style={{ display: 'flex', gap: 4 }}>
            <span style={{
              fontSize: '0.6rem', padding: '1px 5px', borderRadius: 8,
              background: (ORIGIN_COLORS[p.origin] || '#7f8c8d') + '22',
              color: ORIGIN_COLORS[p.origin] || '#7f8c8d',
              fontWeight: 600, textTransform: 'uppercase',
            }}>
              {p.origin}
            </span>
            <span style={{
              fontSize: '0.65rem', padding: '1px 5px', borderRadius: 8,
              background: STATE_COLORS[p.state] + '22', color: STATE_COLORS[p.state],
              fontWeight: 600,
            }}>
              {p.state}
            </span>
          </span>
        </div>
        {!compact && (
          <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 2 }}>
            {p.gap_source || 'directive'} &middot;{' '}
            <span style={{ color: p.item_count === 0 ? '#e8a838' : 'var(--text-dim)' }}>
              {p.item_count} items{p.item_count === 0 ? ' (empty)' : ''}
            </span>
          </div>
        )}
        {!compact && prodLabel && onProducerClick && (
          <div
            onClick={(ev) => { ev.stopPropagation(); onProducerClick(p); }}
            title={`Open ${p.origin} producer page`}
            style={{
              fontSize: '0.65rem', color: ORIGIN_COLORS[p.origin] || 'var(--text-dim)',
              marginTop: 2, cursor: 'pointer',
              textDecoration: 'underline', textDecorationStyle: 'dotted',
              display: 'inline-block',
            }}
          >
            from {prodLabel} &rarr;
          </div>
        )}
        {!compact && p.created_at && (
          <div style={{ fontSize: '0.6rem', color: 'var(--text-dim)', marginTop: 1 }}>
            {new Date(p.created_at).toLocaleString()}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function EvidenceCard({ evidence }: { evidence: GapEvidence | DocumentEvidence | Record<string, unknown> }) {
  const isGapEvidence = 'signal' in evidence && 'metric_value' in evidence;
  const isDocEvidence = 'source' in evidence && 'document' in evidence;

  return (
    <div style={{
      padding: '6px 10px', borderRadius: 6,
      background: 'var(--bg-input)', border: '1px solid var(--border)',
      fontSize: '0.78rem',
    }}>
      {isGapEvidence && (
        <>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            {(evidence as GapEvidence).signal}
            {(evidence as GapEvidence).heightened_review && (
              <span style={{
                marginLeft: 8, padding: '1px 6px', borderRadius: 8, fontSize: '0.62rem',
                background: '#e74c3c22', color: '#e74c3c', fontWeight: 700,
              }}>HEIGHTENED REVIEW</span>
            )}
          </div>
          <div style={{ color: 'var(--text-dim)' }}>{(evidence as GapEvidence).description}</div>
          <div style={{ display: 'flex', gap: 12, marginTop: 4, color: 'var(--text-dim)', fontSize: '0.72rem', flexWrap: 'wrap' }}>
            <span>Value: {(evidence as GapEvidence).metric_value.toFixed(2)}</span>
            <span>Threshold: {(evidence as GapEvidence).threshold.toFixed(2)}</span>
            {(evidence as GapEvidence).neuron_ids.length > 0 && <span>Neurons: {(evidence as GapEvidence).neuron_ids.join(', ')}</span>}
            {(evidence as GapEvidence).query_ids.length > 0 && <span>Queries: {(evidence as GapEvidence).query_ids.join(', ')}</span>}
            {(evidence as GapEvidence).disposition && <span>Disposition: {(evidence as GapEvidence).disposition}</span>}
            {typeof (evidence as GapEvidence).confidence === 'number' && (
              <span>Critic confidence: {((evidence as GapEvidence).confidence as number).toFixed(2)}</span>
            )}
          </div>
          {((evidence as GapEvidence).defect_classes?.length ?? 0) > 0 && (
            <div style={{ marginTop: 4, color: 'var(--text-dim)', fontSize: '0.72rem' }}>
              Defects: {(evidence as GapEvidence).defect_classes!.join(', ')}
            </div>
          )}
          {(evidence as GapEvidence).risk_breakdown && (
            <div style={{ marginTop: 4, fontSize: '0.7rem', color: 'var(--text-dim)' }}>
              {Object.entries((evidence as GapEvidence).risk_breakdown!)
                .sort(([, a], [, b]) => b.score - a.score)
                .map(([name, sig]) => (
                  <div key={name} style={{ fontFamily: 'monospace' }}>
                    {name}={sig.score} — {sig.detail}
                  </div>
                ))}
            </div>
          )}
          {((evidence as GapEvidence).evidence_citations?.length ?? 0) > 0 && (
            <div style={{ marginTop: 4, fontSize: '0.7rem', color: 'var(--text-dim)' }}>
              {(evidence as GapEvidence).evidence_citations!.map((c, i) => (
                <div key={i} style={{ fontStyle: 'italic' }}>&ldquo;{c}&rdquo;</div>
              ))}
            </div>
          )}
          {(evidence as GapEvidence).blast_radius && (
            <div style={{ marginTop: 4, fontSize: '0.72rem', color: 'var(--text-dim)' }}>
              Blast radius: {(evidence as GapEvidence).blast_radius}
            </div>
          )}
          {(evidence as GapEvidence).uncertainty && (
            <div style={{ marginTop: 2, fontSize: '0.72rem', color: 'var(--text-dim)' }}>
              Uncertainty: {(evidence as GapEvidence).uncertainty}
            </div>
          )}
        </>
      )}
      {isDocEvidence && (
        <>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>Document: {String(evidence.document)}</div>
          <div style={{ color: 'var(--text-dim)' }}>Section: {String(evidence.section)}</div>
        </>
      )}
      {!isGapEvidence && !isDocEvidence && (
        <div style={{ color: 'var(--text-dim)' }}>{JSON.stringify(evidence)}</div>
      )}
    </div>
  );
}

interface FusionMemberPreview {
  neuron_id: number;
  content_hash: string;
  node_type?: string | null;
  label?: string | null;
  summary?: string | null;
  content?: string | null;
  is_active: boolean;
  department?: string | null;
  authority_level?: string | null;
  invocations: number;
  avg_utility: number;
  superseded_by?: number | null;
}

interface FusionPlanPreviewData {
  component_member_ids: number[];
  member_snapshots: FusionMemberPreview[];
  disposition: 'retain-canonical' | 'synthesize-new' | 'abstain';
  canonical_neuron_id?: number | null;
  coverage_delta: boolean;
  proposed_node_type?: string | null;
  proposed_department?: string | null;
  proposed_label?: string | null;
  proposed_summary?: string | null;
  proposed_content?: string | null;
  facets?: Array<{ kind: string; text: string; evidence_member_ids: number[]; resolution?: string | null }>;
  inheritance?: {
    invocations_union_distinct: number;
    invocations_member_sum?: number;
    invocations_member_max?: number;
    utility_replayed: number;
    utility_events_replayed: number;
    utility_events_deduped: number;
    utility_provenance_gaps: string[];
    authority_level: string;
    effective_date?: string | null;
    last_verified?: string | null;
    embedding_action: string;
    entities_action: string;
    centrality_action: string;
  } | null;
  rewiring?: {
    internal_activation_edges_to_retire: Array<{ source_id: number; target_id: number; edge_type: string }>;
    provenance_links_to_create: Array<{ source_id: number; target_id: number; edge_type: string }>;
    external_peers: Array<{ peer_id: number; union_cofire_queries: number; recomputed_weight: number; edge_type: string }>;
    inactive_peers_dropped: number[];
  } | null;
}

function FusionPlanPreview({ plan, planHash }: { plan: FusionPlanPreviewData; planHash?: string }) {
  const inheritance = plan.inheritance;
  const rewiring = plan.rewiring;
  const dispositionColor = plan.disposition === 'abstain' ? '#e74c3c'
    : plan.disposition === 'retain-canonical' ? '#e8a838' : '#4caf50';
  const afterTitle = plan.disposition === 'retain-canonical'
    ? `Retain neuron #${plan.canonical_neuron_id}`
    : plan.disposition === 'abstain' ? 'No mutation' : 'Create new synthesis';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
        padding: '9px 11px', borderRadius: 7,
        background: `${dispositionColor}18`, border: `1px solid ${dispositionColor}66`,
      }}>
        <span style={{ color: dispositionColor, fontWeight: 800, letterSpacing: '0.04em' }}>
          {plan.disposition.toUpperCase()}
        </span>
        <span>{afterTitle}</span>
        {plan.coverage_delta && <span style={{ color: 'var(--text-dim)' }}>coverage delta</span>}
        {planHash && <code style={{ marginLeft: 'auto', color: 'var(--text-dim)', fontSize: '0.7rem' }}>plan {planHash.slice(0, 12)}</code>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Before: {plan.member_snapshots.length} members</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {plan.member_snapshots.map(member => {
              const retained = plan.disposition === 'retain-canonical' && member.neuron_id === plan.canonical_neuron_id;
              return (
                <div key={member.neuron_id} style={{ padding: 8, borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong>#{member.neuron_id} {member.label || 'Unlabeled member'}</strong>
                    <span style={{ color: retained ? '#4caf50' : '#e8a838', fontSize: '0.68rem', fontWeight: 700 }}>
                      {retained ? 'RETAIN' : plan.disposition === 'abstain' ? 'UNCHANGED' : 'RETIRE'}
                    </span>
                  </div>
                  <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem', marginTop: 2 }}>
                    {member.department || 'unscoped'} · {member.node_type || 'memory'} · {member.invocations} invocations · utility {member.avg_utility.toFixed(3)}
                  </div>
                  {member.summary && <div style={{ marginTop: 5 }}>{member.summary}</div>}
                  {member.content && <div style={{ marginTop: 5, color: 'var(--text-dim)', whiteSpace: 'pre-wrap' }}>{member.content}</div>}
                  <code style={{ display: 'block', marginTop: 5, color: 'var(--text-dim)', fontSize: '0.65rem' }}>state {member.content_hash}</code>
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>After: {afterTitle}</div>
          <div style={{ padding: 10, borderRadius: 6, background: `${dispositionColor}0f`, border: `1px solid ${dispositionColor}55` }}>
            {plan.disposition === 'retain-canonical' ? (
              <div>The identity and ID of neuron <strong>#{plan.canonical_neuron_id}</strong> survive; its statistics are rebuilt from all members.</div>
            ) : plan.disposition === 'abstain' ? (
              <div>Conflicting or genuinely scoped truths stay separate. Approval cannot mutate this component.</div>
            ) : (
              <>
                <div style={{ fontSize: '0.68rem', color: dispositionColor, fontWeight: 700, textTransform: 'uppercase' }}>
                  {plan.proposed_department || 'unscoped'} · {plan.proposed_node_type || 'lesson'}
                </div>
                <h4 style={{ margin: '5px 0' }}>{plan.proposed_label}</h4>
                {plan.proposed_summary && <div style={{ fontWeight: 600 }}>{plan.proposed_summary}</div>}
                {plan.proposed_content && <div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{plan.proposed_content}</div>}
              </>
            )}
          </div>
          {!!plan.facets?.length && (
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {plan.facets.map((facet, i) => (
                <div key={`${facet.kind}-${i}`} style={{ padding: 6, borderRadius: 5, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <strong style={{ fontSize: '0.68rem', textTransform: 'uppercase' }}>{facet.kind}</strong>{' '}{facet.text}
                  <span style={{ color: 'var(--text-dim)', fontSize: '0.68rem' }}> · evidence #{facet.evidence_member_ids.join(', #')}</span>
                  {facet.resolution && <div style={{ marginTop: 3, color: '#4caf50' }}>Resolution: {facet.resolution}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {inheritance && (
        <div style={{ padding: 10, borderRadius: 7, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Inheritance: rebuilt, not blended</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 7 }}>
            <Metric label="UNION invocations" value={inheritance.invocations_union_distinct} accent="#4caf50" />
            <Metric label="Rejected member sum" value={inheritance.invocations_member_sum ?? 'legacy unavailable'} accent="#e74c3c" />
            <Metric label="Rejected member max" value={inheritance.invocations_member_max ?? 'legacy unavailable'} accent="#e74c3c" />
            <Metric label="Replayed utility" value={inheritance.utility_replayed.toFixed(6)} accent="#4caf50" />
            <Metric label="Authority" value={inheritance.authority_level} />
            <Metric label="Effective / verified" value={`${inheritance.effective_date || 'unknown'} / ${inheritance.last_verified || 'none'}`} />
          </div>
          <div style={{ marginTop: 7, color: 'var(--text-dim)' }}>
            Utility evidence: {inheritance.utility_events_replayed} replayed, {inheritance.utility_events_deduped} same-query events deduped.
          </div>
          {inheritance.utility_provenance_gaps.length > 0 && (
            <div style={{ marginTop: 7, padding: 7, borderRadius: 5, background: '#e8a83814', border: '1px solid #e8a83855' }}>
              <strong>Provenance gaps</strong>
              {inheritance.utility_provenance_gaps.map((gap, i) => <div key={i} style={{ marginTop: 3 }}>· {gap}</div>)}
            </div>
          )}
          <div style={{ marginTop: 7, color: 'var(--text-dim)', fontSize: '0.7rem' }}>
            Embedding: {inheritance.embedding_action} · Entities: {inheritance.entities_action} · Centrality: {inheritance.centrality_action}
          </div>
        </div>
      )}

      {rewiring && (
        <div style={{ padding: 10, borderRadius: 7, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Rewiring preview</div>
          <div>{rewiring.internal_activation_edges_to_retire.length} internal conducting edges retire · {rewiring.provenance_links_to_create.length} evidence links preserve lineage</div>
          <div style={{ marginTop: 7, fontWeight: 600 }}>External peers ({rewiring.external_peers.length})</div>
          <div style={{ marginTop: 4, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 4, maxHeight: 220, overflow: 'auto' }}>
            {rewiring.external_peers.map(peer => (
              <div key={peer.peer_id} style={{ padding: 5, borderRadius: 4, background: 'var(--bg-input)' }}>
                <strong>#{peer.peer_id}</strong> · UNION co-fire {peer.union_cofire_queries} · weight {peer.recomputed_weight.toFixed(3)} · {peer.edge_type}
              </div>
            ))}
            {rewiring.external_peers.length === 0 && <span style={{ color: 'var(--text-dim)' }}>No external conducting peers.</span>}
          </div>
          <div style={{ marginTop: 7, color: rewiring.inactive_peers_dropped.length ? '#e8a838' : 'var(--text-dim)' }}>
            Dropped inactive/superseded peers: {rewiring.inactive_peers_dropped.length ? `#${rewiring.inactive_peers_dropped.join(', #')}` : 'none'}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, accent = 'var(--text)' }: { label: string; value: string | number; accent?: string }) {
  return (
    <div style={{ padding: 7, borderRadius: 5, background: 'var(--bg-input)' }}>
      <div style={{ color: 'var(--text-dim)', fontSize: '0.66rem', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ color: accent, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  );
}

// ── Server-rendered FusionPlan review card (mind-fusionplan-preview-ui) ──
// Presentation only: every value here was computed server-side by
// services/reconsolidation/render.py from the hashed plan + live graph.

const MEMBER_STATUS_META: Record<RenderedFusionMember['status'], { color: string; label: string }> = {
  'fresh': { color: '#4caf50', label: '✓ fresh' },
  'content-drifted': { color: '#e8a838', label: '⚠ content drifted' },
  'state-drifted': { color: '#e8a838', label: '⚠ state drifted' },
  'superseded': { color: '#e74c3c', label: '☠ superseded' },
  'missing': { color: '#e74c3c', label: '☠ missing' },
};

function RenderedFusionCard({ rp, proposalState }: { rp: RenderedFusionPlan; proposalState?: string }) {
  const [showPostconditions, setShowPostconditions] = useState(false);
  const stale = rp.freshness?.verdict === 'stale';
  const reviewable = proposalState === 'proposed';
  const disposition = rp.disposition ?? 'synthesize-new';
  const dispositionColor = disposition === 'abstain' ? '#e74c3c'
    : disposition === 'retain-canonical' ? '#e8a838' : '#4caf50';
  const afterTitle = disposition === 'retain-canonical'
    ? `Retain neuron #${rp.canonical_neuron_id}`
    : disposition === 'abstain' ? 'No mutation (abstain)' : 'Create new synthesis';

  const fieldsByName = new Map((rp.fields ?? []).map(f => [f.field, f]));
  const identity = {
    label: fieldsByName.get('label')?.after,
    summary: fieldsByName.get('summary')?.after,
    content: fieldsByName.get('content')?.after,
    scope: fieldsByName.get('scope')?.after,
  };
  const statReceipts = (rp.fields ?? []).filter(f => !['label', 'summary', 'content', 'scope'].includes(f.field));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Disposition + hash receipts */}
      <div style={{
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
        padding: '9px 11px', borderRadius: 7,
        background: `${dispositionColor}18`, border: `1px solid ${dispositionColor}66`,
      }}>
        <span style={{ color: dispositionColor, fontWeight: 800, letterSpacing: '0.04em' }}>
          {disposition.toUpperCase()}
        </span>
        <span>{afterTitle}</span>
        {rp.coverage_delta && <span style={{ color: 'var(--text-dim)' }}>coverage delta</span>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {rp.plan_hash && <code style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }} title={rp.plan_hash}>plan {rp.plan_hash.slice(0, 12)}</code>}
          {rp.member_state_hash && <code style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }} title={rp.member_state_hash}>members {rp.member_state_hash.slice(0, 12)}</code>}
        </span>
      </div>

      {/* Freshness verdict — a stale plan on a reviewable proposal is DEAD */}
      {stale && reviewable ? (
        <div style={{ padding: 11, borderRadius: 7, background: '#e74c3c1c', border: '2px solid #e74c3c' }}>
          <div style={{ color: '#e74c3c', fontWeight: 800, letterSpacing: '0.04em' }}>
            ☠ DEAD PLAN — member state drifted since review
          </div>
          <div style={{ marginTop: 5 }}>
            The graph this plan pinned no longer exists. Reviewing it now will <strong>terminally supersede</strong> it (nothing applies); the janitor will re-propose from live state if the duplication still holds.
          </div>
          {(rp.freshness?.dead_targets ?? []).map(dt => (
            <div key={dt.neuron_id} style={{ marginTop: 6, padding: 6, borderRadius: 5, background: 'var(--bg-card)' }}>
              <strong>#{dt.neuron_id}</strong> {dt.note} — the fact this plan wanted to fuse now lives in <strong>#{dt.superseded_by}</strong>.
            </div>
          ))}
          {(rp.freshness?.violations ?? []).map((v, i) => (
            <div key={i} style={{ marginTop: 4, color: 'var(--text-dim)', fontSize: '0.75rem' }}>· {v}</div>
          ))}
        </div>
      ) : stale ? (
        <div style={{ padding: 8, borderRadius: 6, background: 'var(--bg-card)', border: '1px dashed var(--border)', color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          Members have drifted since this proposal settled ({proposalState}) — expected for historical rows; receipts below show the state the reviewer signed.
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#4caf50', fontSize: '0.78rem', fontWeight: 600 }}>
          ✓ members pinned fresh — live graph matches the reviewed state hash
        </div>
      )}

      {/* Before / After */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Before: {(rp.members ?? []).length} members</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {(rp.members ?? []).map(m => {
              const meta = MEMBER_STATUS_META[m.status] ?? MEMBER_STATUS_META['fresh'];
              return (
                <div key={m.neuron_id} style={{
                  padding: 8, borderRadius: 6, background: 'var(--bg-card)',
                  border: `1px solid ${m.status === 'fresh' ? 'var(--border)' : meta.color + '88'}`,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                    <strong>#{m.neuron_id} {m.label || 'Unlabeled member'}</strong>
                    <span style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                      <span style={{ color: meta.color, fontSize: '0.68rem', fontWeight: 700 }}>{meta.label}</span>
                      <span style={{ color: m.outcome === 'retain' ? '#4caf50' : '#e8a838', fontSize: '0.68rem', fontWeight: 700 }}>
                        {m.outcome.toUpperCase()}
                      </span>
                    </span>
                  </div>
                  <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem', marginTop: 2 }}>
                    {m.scope || 'unscoped'} · {m.node_type || 'memory'} · {m.authority_level || 'informational'} · {m.invocations} invocations · utility {m.avg_utility.toFixed(3)} · cited by {m.facet_evidence_count} facet{m.facet_evidence_count === 1 ? '' : 's'}
                  </div>
                  {m.status_notes.length > 0 && (
                    <div style={{ marginTop: 4, color: meta.color, fontSize: '0.7rem' }}>
                      {m.status_notes.map((n, i) => <div key={i}>· {n}</div>)}
                    </div>
                  )}
                  {m.summary && <div style={{ marginTop: 5 }}>{m.summary}</div>}
                  {m.content && <div style={{ marginTop: 5, color: 'var(--text-dim)', whiteSpace: 'pre-wrap' }}>{m.content}</div>}
                  <code style={{ display: 'block', marginTop: 5, color: 'var(--text-dim)', fontSize: '0.65rem' }}>state {m.content_hash}</code>
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>After: {afterTitle}</div>
          <div style={{ padding: 10, borderRadius: 6, background: `${dispositionColor}0f`, border: `1px solid ${dispositionColor}55` }}>
            {disposition === 'abstain' ? (
              <div>Conflicting or genuinely scoped truths stay separate. Approval cannot mutate this component.</div>
            ) : (
              <>
                <div style={{ fontSize: '0.68rem', color: dispositionColor, fontWeight: 700, textTransform: 'uppercase' }}>
                  {String(identity.scope || 'unscoped')} · {rp.proposed_node_type || 'lesson'}
                </div>
                <h4 style={{ margin: '5px 0' }}>{String(identity.label ?? '')}</h4>
                {identity.summary != null && <div style={{ fontWeight: 600 }}>{String(identity.summary)}</div>}
                {identity.content != null && <div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{String(identity.content)}</div>}
                {disposition === 'retain-canonical' && (
                  <div style={{ marginTop: 8, color: 'var(--text-dim)', fontSize: '0.72rem' }}>
                    Identity and ID of #{rp.canonical_neuron_id} survive; statistics rebuild from all members.
                  </div>
                )}
              </>
            )}
          </div>
          {!!rp.facets?.length && (
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {rp.facets.map((facet, i) => (
                <div key={`${facet.kind}-${i}`} style={{ padding: 6, borderRadius: 5, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <strong style={{ fontSize: '0.68rem', textTransform: 'uppercase' }}>{facet.kind}</strong>{' '}{facet.text}
                  <span style={{ color: 'var(--text-dim)', fontSize: '0.68rem' }}> · evidence #{facet.evidence_member_ids.join(', #')}</span>
                  {facet.resolution && <div style={{ marginTop: 3, color: '#4caf50' }}>Resolution: {facet.resolution}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Field-inheritance receipts: value + WHICH rule fired */}
      {statReceipts.length > 0 && (
        <div style={{ padding: 10, borderRadius: 7, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Inheritance receipts — one rule per signal, never max/mean</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {statReceipts.map(f => <ReceiptRow key={f.field} receipt={f} />)}
          </div>
        </div>
      )}

      {/* Rewiring summary */}
      {rp.rewiring && (
        <div style={{ padding: 10, borderRadius: 7, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Rewiring</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 7 }}>
            <Metric label="Internal conducting deleted" value={rp.rewiring.internal_conducting_deleted} accent="#e74c3c" />
            <Metric label="Member↔peer retired" value={rp.rewiring.member_peer_retired} accent="#e8a838" />
            <Metric label="Synthesis↔peer created" value={rp.rewiring.synthesis_peer_created} accent="#4caf50" />
            <Metric label="Provenance links kept" value={rp.rewiring.provenance_links_created} />
          </div>
          <div style={{ marginTop: 7, color: 'var(--text-dim)', fontSize: '0.72rem' }}>{rp.rewiring.why}</div>
          {rp.rewiring.peers.length > 0 && (
            <div style={{ marginTop: 7, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 4, maxHeight: 220, overflow: 'auto' }}>
              {rp.rewiring.peers.map(peer => (
                <div key={peer.peer_id} style={{ padding: 5, borderRadius: 4, background: 'var(--bg-input)' }}>
                  <strong>#{peer.peer_id}</strong> · weight {peer.recomputed_weight.toFixed(3)} · {peer.edge_type}
                  <div style={{ color: 'var(--text-dim)', fontSize: '0.68rem', marginTop: 2 }}>{peer.weight_provenance}</div>
                </div>
              ))}
            </div>
          )}
          <div style={{ marginTop: 7, color: rp.rewiring.inactive_peers_dropped.length ? '#e8a838' : 'var(--text-dim)', fontSize: '0.75rem' }}>
            Dropped inactive/superseded peers: {rp.rewiring.inactive_peers_dropped.length ? `#${rp.rewiring.inactive_peers_dropped.join(', #')}` : 'none'}
          </div>
        </div>
      )}

      {/* Validator status */}
      {rp.validators && (
        <div style={{
          padding: 10, borderRadius: 7, background: 'var(--bg-card)',
          border: `1px solid ${rp.validators.preflight_passed ? 'var(--border)' : '#e74c3c88'}`,
        }}>
          <div style={{ fontWeight: 700, color: rp.validators.preflight_passed ? '#4caf50' : '#e74c3c' }}>
            {rp.validators.preflight_passed
              ? '✓ Preflight clean — plan is appliable against the live graph'
              : `✗ Preflight: ${rp.validators.preflight_violations.length} violation(s) — approval would fail closed`}
          </div>
          {!rp.validators.preflight_passed && (
            <div style={{ marginTop: 5 }}>
              {rp.validators.preflight_violations.map((v, i) => (
                <div key={i} style={{ color: '#e74c3c', fontSize: '0.75rem', marginTop: 2 }}>· {v}</div>
              ))}
            </div>
          )}
          <button
            onClick={() => setShowPostconditions(s => !s)}
            style={{
              marginTop: 7, fontSize: '0.7rem', padding: '2px 8px',
              background: 'transparent', border: '1px dashed var(--border)',
              color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
            }}
          >
            {showPostconditions ? 'Hide' : 'Show'} postconditions asserted inside the apply transaction ({rp.validators.postconditions_asserted_at_apply.length})
          </button>
          {showPostconditions && (
            <div style={{ marginTop: 5, color: 'var(--text-dim)', fontSize: '0.73rem' }}>
              {rp.validators.postconditions_asserted_at_apply.map((p, i) => (
                <div key={i} style={{ marginTop: 2 }}>· {p} </div>
              ))}
              <div style={{ marginTop: 4, fontStyle: 'italic' }}>Any violation rolls back the whole transaction, approval included.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ReceiptRow({ receipt }: { receipt: RenderedFieldReceipt }) {
  return (
    <div style={{ padding: 7, borderRadius: 5, background: 'var(--bg-input)' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.68rem', textTransform: 'uppercase', minWidth: 110 }}>{receipt.field}</span>
        <strong style={{ color: '#4caf50' }}>{String(receipt.after ?? '—')}</strong>
        <code style={{ fontSize: '0.66rem', padding: '1px 6px', borderRadius: 8, background: 'var(--bg-card)', color: 'var(--text-dim)' }}>{receipt.rule}</code>
        {receipt.rejected && Object.entries(receipt.rejected).map(([k, v]) => (
          <span key={k} style={{ color: '#e74c3c', fontSize: '0.7rem' }} title="Tempting but rejected alternative — receipt, never an apply input">
            ✗ {k.replace('member_', '')} {v}
          </span>
        ))}
      </div>
      <div style={{ marginTop: 3, color: 'var(--text-dim)', fontSize: '0.72rem' }}>{receipt.why}</div>
      {!!receipt.flags?.length && (
        <div style={{ marginTop: 4, padding: 6, borderRadius: 4, background: '#e8a83814', border: '1px solid #e8a83855', fontSize: '0.72rem' }}>
          <strong style={{ color: '#e8a838' }}>Provenance gaps (reported, never laundered)</strong>
          {receipt.flags.map((g, i) => <div key={i} style={{ marginTop: 2 }}>· {g}</div>)}
        </div>
      )}
    </div>
  );
}

function ItemCard({ item, proposalState, onOpenDiff }: { item: ProposalItem; proposalState?: string; onOpenDiff: () => void }) {
  let spec: Record<string, any> | null = null;
  try { spec = item.neuron_spec_json ? JSON.parse(item.neuron_spec_json) : null; }
  catch { spec = null; }
  const [expanded, setExpanded] = useState(false);

  const oldLen = item.old_value?.length ?? 0;
  const newLen = item.new_value?.length ?? 0;
  const truncated = oldLen > 400 || newLen > 400;

  // Server-rendered review projection is authoritative when present; the
  // client-side FusionPlanPreview parse stays as the fallback for legacy
  // responses (and for a projection the server could not render).
  if (item.action === 'reconsolidate' && item.rendered_plan && !item.rendered_plan.error) {
    return (
      <div style={{ padding: 10, borderRadius: 7, marginBottom: 6, background: 'var(--bg-input)', border: '1px solid var(--border)', fontSize: '0.8rem' }}>
        <RenderedFusionCard rp={item.rendered_plan} proposalState={proposalState} />
        {item.reason && <div style={{ marginTop: 8, color: 'var(--text-dim)', fontStyle: 'italic' }}>{item.reason}</div>}
      </div>
    );
  }

  if (item.action === 'reconsolidate' && spec?.fusion_plan) {
    return (
      <div style={{ padding: 10, borderRadius: 7, marginBottom: 6, background: 'var(--bg-input)', border: '1px solid var(--border)', fontSize: '0.8rem' }}>
        {item.rendered_plan?.error && (
          <div style={{ marginBottom: 8, padding: 7, borderRadius: 5, background: '#e74c3c14', border: '1px solid #e74c3c55', color: '#e74c3c' }}>
            ⚠ {item.rendered_plan.error} — showing client-side preview.
          </div>
        )}
        <FusionPlanPreview plan={spec.fusion_plan as FusionPlanPreviewData} planHash={spec.plan_hash as string | undefined} />
        {item.reason && <div style={{ marginTop: 8, color: 'var(--text-dim)', fontStyle: 'italic' }}>{item.reason}</div>}
      </div>
    );
  }

  return (
    <div style={{
      padding: '8px 10px', borderRadius: 6, marginBottom: 6,
      background: 'var(--bg-input)', border: '1px solid var(--border)',
      fontSize: '0.8rem',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <span style={{
          fontWeight: 600,
          color: item.action === 'create' ? '#4caf50' : '#e8a838',
        }}>
          {item.action.toUpperCase()}
        </span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {item.target_neuron_id && <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>Neuron #{item.target_neuron_id}</span>}
          {item.created_neuron_id && <span style={{ color: '#4caf50', fontSize: '0.75rem' }}>Created #{item.created_neuron_id}</span>}
          {(item.old_value || item.new_value) && (
            <button
              onClick={onOpenDiff}
              style={{
                fontSize: '0.7rem', padding: '2px 8px', borderRadius: 4,
                border: '1px solid var(--border)', background: 'var(--bg-card)',
                color: 'var(--text)', cursor: 'pointer',
              }}
            >View full diff</button>
          )}
        </span>
      </div>

      {item.action === 'update' && (
        <>
          <div><strong>Field:</strong> {item.field}</div>
          {(item.old_value || item.new_value) && (
            <div style={{ marginTop: 4 }}>
              {truncated && !expanded ? (
                <>
                  <div style={{ color: '#e74c3c', fontSize: '0.75rem' }}>- {(item.old_value ?? '').slice(0, 400)}{oldLen > 400 ? '…' : ''}</div>
                  <div style={{ color: '#4caf50', fontSize: '0.75rem' }}>+ {(item.new_value ?? '').slice(0, 400)}{newLen > 400 ? '…' : ''}</div>
                  <button
                    onClick={() => setExpanded(true)}
                    style={{
                      marginTop: 4, fontSize: '0.7rem', padding: '2px 6px',
                      background: 'transparent', border: '1px dashed var(--border)',
                      color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
                    }}
                  >Expand ({oldLen + newLen} chars)</button>
                </>
              ) : (
                <>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#e74c3c', fontSize: '0.75rem' }}>- {item.old_value ?? ''}</pre>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#4caf50', fontSize: '0.75rem' }}>+ {item.new_value ?? ''}</pre>
                  {truncated && (
                    <button
                      onClick={() => setExpanded(false)}
                      style={{
                        marginTop: 4, fontSize: '0.7rem', padding: '2px 6px',
                        background: 'transparent', border: '1px dashed var(--border)',
                        color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
                      }}
                    >Collapse</button>
                  )}
                </>
              )}
            </div>
          )}
        </>
      )}

      {item.action === 'create' && spec && (
        <div style={{ fontSize: '0.75rem' }}>
          <div><strong>Label:</strong> {spec.label}</div>
          <div><strong>Layer:</strong> {spec.layer} &middot; <strong>Type:</strong> {spec.node_type}</div>
          {spec.department && <div><strong>Dept:</strong> {spec.department}</div>}
          {spec.summary && <div style={{ marginTop: 2, color: 'var(--text-dim)' }}>{spec.summary}</div>}
          {spec.content && (
            <div style={{ marginTop: 4, padding: 6, background: 'var(--bg-card)', borderRadius: 4, maxHeight: expanded ? 400 : 100, overflow: 'auto' }}>
              {expanded ? spec.content : spec.content.slice(0, 300) + (spec.content.length > 300 ? '…' : '')}
            </div>
          )}
          {spec.content && spec.content.length > 300 && (
            <button
              onClick={() => setExpanded(e => !e)}
              style={{
                marginTop: 4, fontSize: '0.7rem', padding: '2px 6px',
                background: 'transparent', border: '1px dashed var(--border)',
                color: 'var(--text-dim)', borderRadius: 4, cursor: 'pointer',
              }}
            >{expanded ? 'Collapse' : `Expand (${spec.content.length} chars)`}</button>
          )}
        </div>
      )}

      {item.reason && (
        <div style={{ marginTop: 4, fontSize: '0.75rem', color: 'var(--text-dim)', fontStyle: 'italic' }}>
          {item.reason}
        </div>
      )}
    </div>
  );
}

// Pretty-print a value as JSON if it parses, otherwise return as-is.
function maybePretty(s: string | null | undefined): string {
  if (!s) return '';
  const trimmed = s.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return s;
  try { return JSON.stringify(JSON.parse(trimmed), null, 2); }
  catch { return s; }
}

// Word-level diff of the two panes: deletions marked red on the left,
// additions green on the right, unchanged text plain in both.
function renderDiff(oldText: string, newText: string): [ReactNode[], ReactNode[]] {
  const parts = diffWords(oldText || '', newText || '');
  const left: ReactNode[] = [];
  const right: ReactNode[] = [];
  parts.forEach((part, i) => {
    if (part.removed) {
      left.push(<mark key={i} style={{ background: '#fca5a5', color: '#7f1d1d' }}>{part.value}</mark>);
    } else if (part.added) {
      right.push(<mark key={i} style={{ background: '#86efac', color: '#14532d' }}>{part.value}</mark>);
    } else {
      left.push(<span key={`l${i}`}>{part.value}</span>);
      right.push(<span key={`r${i}`}>{part.value}</span>);
    }
  });
  return [left, right];
}

function DiffModal({ item, onClose }: { item: ProposalItem; onClose: () => void }) {
  const oldText = item.action === 'create'
    ? ''
    : maybePretty(item.old_value);
  const newText = item.action === 'create'
    ? maybePretty(item.neuron_spec_json)
    : maybePretty(item.new_value);
  const [leftDiff, rightDiff] = renderDiff(oldText, newText);

  return (
    <div
      className="pq-modal-shade"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        className="pq-modal"
        onClick={e => e.stopPropagation()}
        style={{
          width: '90vw', maxWidth: 1200, height: '80vh',
          background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 8, display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '10px 14px', borderBottom: '1px solid var(--border)',
        }}>
          <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>
            {item.action.toUpperCase()}
            {item.field ? ` · ${item.field}` : ''}
            {item.target_neuron_id ? ` · neuron #${item.target_neuron_id}` : ''}
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: '1px solid var(--border)',
              color: 'var(--text)', padding: '3px 10px', borderRadius: 4,
              cursor: 'pointer', fontSize: '0.8rem',
            }}
          >Close (Esc)</button>
        </div>
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          <div style={{ flex: 1, borderRight: '1px solid var(--border)', overflow: 'auto', padding: 12 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#e74c3c', marginBottom: 6 }}>
              - BEFORE ({oldText.length} chars)
            </div>
            <pre style={{
              margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              fontSize: '0.78rem', fontFamily: 'monospace',
              color: 'var(--text)',
            }}>{oldText ? leftDiff : <span style={{ color: 'var(--text-dim)' }}>(empty)</span>}</pre>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#4caf50', marginBottom: 6 }}>
              + AFTER ({newText.length} chars)
            </div>
            <pre style={{
              margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              fontSize: '0.78rem', fontFamily: 'monospace',
              color: 'var(--text)',
            }}>{newText ? rightDiff : <span style={{ color: 'var(--text-dim)' }}>(empty)</span>}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function Cheatsheet({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ['j / k', 'Next / previous proposal'],
    ['d', 'Open diff for focused proposal'],
    ['a', 'Approve & apply (one step)'],
    ['r', 'Reject focused proposal'],
    ['y', 'Apply (when approved)'],
    ['x', 'Toggle focused in multi-select'],
    ['/', 'Focus state filter'],
    ['?', 'Toggle this cheatsheet'],
    ['Esc', 'Close modal / cheatsheet'],
  ];
  return (
    <div
      className="pq-modal-shade"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1001,
      }}
    >
      <div
        className="pq-modal"
        onClick={e => e.stopPropagation()}
        style={{
          minWidth: 320, background: 'var(--bg-card)', border: '1px solid var(--border)',
          borderRadius: 8, padding: 16,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Keyboard shortcuts</div>
        <table style={{ fontSize: '0.82rem', borderCollapse: 'collapse' }}>
          <tbody>
            {rows.map(([k, d]) => (
              <tr key={k}>
                <td style={{ padding: '3px 10px 3px 0' }}>
                  <kbd style={{
                    padding: '1px 6px', borderRadius: 3,
                    background: 'rgba(255,255,255,0.08)',
                    border: '1px solid var(--border)', fontFamily: 'monospace',
                  }}>{k}</kbd>
                </td>
                <td style={{ padding: '3px 0', color: 'var(--text-dim)' }}>{d}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: 10 }}>
          Shortcuts ignored while typing in an input or textarea.
        </div>
      </div>
    </div>
  );
}
