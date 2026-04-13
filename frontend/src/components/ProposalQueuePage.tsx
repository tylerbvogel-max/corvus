import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  fetchProposals,
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
  type Whoami,
} from '../api';
import { getReviewerName, setReviewerName } from '../auth';

type StateFilter = 'all' | 'proposed' | 'approved' | 'rejected' | 'applied';
type OriginFilter = 'all' | 'autopilot' | 'integrity' | 'document' | 'manual';

const STATE_COLORS: Record<string, string> = {
  proposed: '#e8a838',
  approved: '#4caf50',
  rejected: '#e74c3c',
  applied: '#2196f3',
};

const ORIGIN_COLORS: Record<string, string> = {
  autopilot: '#9b59b6',
  integrity: '#2196f3',
  document: '#4caf50',
  manual: '#7f8c8d',
};

const SOURCE_OPTIONS: { key: OriginFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'autopilot', label: 'Autopilot' },
  { key: 'integrity', label: 'Integrity' },
  { key: 'document', label: 'Document' },
  { key: 'manual', label: 'Manual' },
];

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
  borderRadius: 4, padding: '4px 8px', fontSize: '0.8rem',
};

const inputStyle: React.CSSProperties = {
  ...selectStyle, width: '100%', boxSizing: 'border-box' as const,
};

// Client-side grouping key — approximates semantic-cluster bulk review without
// a schema change. Proposals with the same (origin, gap_source) typically have
// the same structural fix (e.g. "missing provenance" on 12 neurons) and are
// safe to approve together after spot-check. True cosine-dedup is deferred.
function groupKey(p: ProposalSummary): string {
  return `${p.origin}::${p.gap_source || 'directive'}`;
}

function groupLabel(p: ProposalSummary): string {
  return `${p.origin} · ${p.gap_source || 'directive'}`;
}

export default function ProposalQueuePage() {
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const [stats, setStats] = useState<ProposalStats | null>(null);
  const [selected, setSelected] = useState<ProposalDetail | null>(null);
  const [filter, setFilter] = useState<StateFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<OriginFilter>('all');
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

  const handleReview = useCallback(async (action: 'approve' | 'reject', applyAfter = false) => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      const updated = await reviewProposal(selected.id, action, reviewer.trim(), reviewNotes);
      if (applyAfter && action === 'approve') {
        const applied = await applyProposal(updated.id, reviewer.trim());
        setSelected(applied);
      } else {
        setSelected(updated);
      }
      await load();
    } catch (e) {
      setError(String(e));
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
  const handleBulk = useCallback(async (action: 'approve' | 'reject' | 'apply') => {
    if (selectedIds.size === 0 || !reviewer.trim()) return;
    const ids = Array.from(selectedIds);
    setActionLoading(true);
    let done = 0;
    const failures: number[] = [];
    const verb = action === 'approve' ? 'Approving' : action === 'reject' ? 'Rejecting' : 'Applying';
    const pastVerb = action === 'approve' ? 'approved' : action === 'reject' ? 'rejected' : 'applied';
    for (const id of ids) {
      setBulkProgress(`${verb} ${done + 1}/${ids.length}...`);
      try {
        if (action === 'apply') {
          await applyProposal(id, reviewer.trim());
        } else {
          await reviewProposal(id, action, reviewer.trim(), reviewNotes);
        }
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

  // Grouped view for cluster-approve ergonomics.
  const grouped = useMemo(() => {
    const m = new Map<string, ProposalSummary[]>();
    for (const p of proposals) {
      const k = groupKey(p);
      const arr = m.get(k);
      if (arr) arr.push(p);
      else m.set(k, [p]);
    }
    return Array.from(m.entries());
  }, [proposals]);

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

      if (e.key === 'j' || e.key === 'k') {
        e.preventDefault();
        if (proposals.length === 0) return;
        const curIdx = selected ? proposals.findIndex(p => p.id === selected.id) : -1;
        const nextIdx = e.key === 'j'
          ? Math.min(proposals.length - 1, curIdx + 1)
          : Math.max(0, curIdx < 0 ? 0 : curIdx - 1);
        const target = proposals[nextIdx];
        if (target) void selectProposal(target.id);
        return;
      }

      if (e.key === 'x' && selected) {
        e.preventDefault();
        toggleSelectId(selected.id);
        return;
      }

      if ((e.key === 'a' || e.key === 'A') && selected && selected.state === 'proposed') {
        e.preventDefault();
        if (!reviewer.trim()) { reviewerInputRef.current?.focus(); return; }
        void handleReview('approve', e.key === 'A');
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
  }, [proposals, selected, reviewer, showCheatsheet, diffItem, selectProposal, handleReview, handleApply, toggleSelectId]);

  const pendingCount = stats?.proposed ?? 0;

  return (
    <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 120px)' }}>
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
                {(['proposed', 'approved', 'rejected', 'applied'] as const).map(s => (
                  <span key={s} style={{
                    padding: '1px 6px', borderRadius: 10,
                    background: STATE_COLORS[s] + '22', color: STATE_COLORS[s],
                    fontWeight: 600,
                  }}>
                    {s}: {stats[s]}
                  </span>
                ))}
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
                  >Approve all</button>
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
                    onClick={() => handleBulk('apply')}
                    disabled={actionLoading || !reviewer.trim()}
                    title="Apply approved selections to graph (non-approved will be skipped)"
                    style={{
                      flex: 1, padding: '4px 6px', borderRadius: 4, border: 'none',
                      background: '#2196f3', color: '#fff', cursor: 'pointer',
                      fontSize: '0.72rem', fontWeight: 600,
                      opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                    }}
                  >Apply all</button>
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
                        {collapsed ? '▸' : '▾'} {groupLabel(rows[0])} <span style={{ color: 'var(--text-dim)', fontWeight: 400 }}>· {rows.length}</span>
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
                <ItemCard key={item.id} item={item} onOpenDiff={() => setDiffItem(item)} />
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
                      style={{
                        flex: 1, padding: '8px 16px', borderRadius: 6, border: 'none',
                        background: '#4caf50', color: '#fff', cursor: 'pointer',
                        fontWeight: 600,
                        opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                      }}
                    >
                      Approve <kbd style={kbdInline}>a</kbd>
                    </button>
                    <button
                      onClick={() => handleReview('approve', true)}
                      disabled={actionLoading || !reviewer.trim()}
                      title="Approve and apply to graph"
                      style={{
                        flex: 1, padding: '8px 16px', borderRadius: 6, border: 'none',
                        background: '#2196f3', color: '#fff', cursor: 'pointer',
                        fontWeight: 600,
                        opacity: actionLoading || !reviewer.trim() ? 0.5 : 1,
                      }}
                    >
                      Approve & Apply <kbd style={kbdInline}>A</kbd>
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

function ProposalRow({
  p, isFocused, isChecked, onOpen, onCheck, compact,
}: {
  p: ProposalSummary;
  isFocused: boolean;
  isChecked: boolean;
  onOpen: () => void;
  onCheck: () => void;
  compact?: boolean;
}) {
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

function EvidenceCard({ evidence }: { evidence: GapEvidence | Record<string, unknown> }) {
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
          <div style={{ fontWeight: 600, marginBottom: 2 }}>{(evidence as GapEvidence).signal}</div>
          <div style={{ color: 'var(--text-dim)' }}>{(evidence as GapEvidence).description}</div>
          <div style={{ display: 'flex', gap: 12, marginTop: 4, color: 'var(--text-dim)', fontSize: '0.72rem' }}>
            <span>Value: {(evidence as GapEvidence).metric_value.toFixed(2)}</span>
            <span>Threshold: {(evidence as GapEvidence).threshold.toFixed(2)}</span>
            {(evidence as GapEvidence).neuron_ids.length > 0 && <span>Neurons: {(evidence as GapEvidence).neuron_ids.join(', ')}</span>}
            {(evidence as GapEvidence).query_ids.length > 0 && <span>Queries: {(evidence as GapEvidence).query_ids.join(', ')}</span>}
          </div>
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

function ItemCard({ item, onOpenDiff }: { item: ProposalItem; onOpenDiff: () => void }) {
  const spec = item.neuron_spec_json ? JSON.parse(item.neuron_spec_json) : null;
  const [expanded, setExpanded] = useState(false);

  const oldLen = item.old_value?.length ?? 0;
  const newLen = item.new_value?.length ?? 0;
  const truncated = oldLen > 400 || newLen > 400;

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

function DiffModal({ item, onClose }: { item: ProposalItem; onClose: () => void }) {
  const oldText = item.action === 'create'
    ? ''
    : maybePretty(item.old_value);
  const newText = item.action === 'create'
    ? maybePretty(item.neuron_spec_json)
    : maybePretty(item.new_value);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
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
            }}>{oldText || <span style={{ color: 'var(--text-dim)' }}>(empty)</span>}</pre>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#4caf50', marginBottom: 6 }}>
              + AFTER ({newText.length} chars)
            </div>
            <pre style={{
              margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              fontSize: '0.78rem', fontFamily: 'monospace',
              color: 'var(--text)',
            }}>{newText || <span style={{ color: 'var(--text-dim)' }}>(empty)</span>}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function Cheatsheet({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ['j / k', 'Next / previous proposal'],
    ['a', 'Approve focused proposal'],
    ['Shift+A', 'Approve & apply to graph'],
    ['r', 'Reject focused proposal'],
    ['y', 'Apply (when approved)'],
    ['x', 'Toggle focused in multi-select'],
    ['/', 'Focus state filter'],
    ['?', 'Toggle this cheatsheet'],
    ['Esc', 'Close modal / cheatsheet'],
  ];
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1001,
      }}
    >
      <div
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
