import { useState, useEffect, useCallback } from 'react';
import {
  fetchProposals,
  fetchProposalDetail,
  reviewProposal,
  applyProposal,
  fetchProposalStats,
  type ProposalSummary,
  type ProposalDetail,
  type ProposalStats,
  type ProposalItem,
  type GapEvidence,
} from '../api';

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

export default function ProposalQueuePage() {
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const [stats, setStats] = useState<ProposalStats | null>(null);
  const [selected, setSelected] = useState<ProposalDetail | null>(null);
  const [filter, setFilter] = useState<StateFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<OriginFilter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);

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

  const selectProposal = async (id: number) => {
    try {
      const detail = await fetchProposalDetail(id);
      setSelected(detail);
      setReviewNotes('');
    } catch (e) {
      setError(String(e));
    }
  };

  const handleReview = async (action: 'approve' | 'reject') => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      const updated = await reviewProposal(selected.id, action, reviewer.trim(), reviewNotes);
      setSelected(updated);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleApply = async () => {
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
  };

  const pendingCount = stats?.proposed ?? 0;

  return (
    <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 120px)' }}>
      {/* Left Panel — Collapsible Queue */}
      <div style={{
        width: queueCollapsed ? 40 : 340, flexShrink: 0,
        display: 'flex', flexDirection: 'column',
        borderRight: '1px solid var(--border)',
        transition: 'width 0.2s ease',
        overflow: 'hidden',
      }}>
        {/* Collapse toggle */}
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
        </button>

        {!queueCollapsed && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 8px', flex: 1, overflow: 'hidden' }}>
            {/* Stats bar */}
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

            {/* Source pills */}
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

            {/* State filter */}
            <select value={filter} onChange={e => setFilter(e.target.value as StateFilter)} style={selectStyle}>
              <option value="all">All States</option>
              <option value="proposed">Proposed</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="applied">Applied</option>
            </select>

            {/* List */}
            <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {loading && <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>Loading...</div>}
              {error && <div style={{ color: '#e74c3c', padding: 8, fontSize: '0.8rem' }}>{error}</div>}
              {!loading && proposals.length === 0 && (
                <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>No proposals found.</div>
              )}
              {proposals.map(p => (
                <div
                  key={p.id}
                  onClick={() => selectProposal(p.id)}
                  style={{
                    padding: '6px 8px', borderRadius: 6, cursor: 'pointer',
                    background: selected?.id === p.id ? 'var(--bg-active)' : 'var(--bg-card)',
                    border: `1px solid ${selected?.id === p.id ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >
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
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 2 }}>
                    {p.gap_source || 'directive'} &middot;{' '}
                    <span style={{ color: p.item_count === 0 ? '#e8a838' : 'var(--text-dim)' }}>
                      {p.item_count} items{p.item_count === 0 ? ' (empty)' : ''}
                    </span>
                  </div>
                  {p.created_at && (
                    <div style={{ fontSize: '0.6rem', color: 'var(--text-dim)', marginTop: 1 }}>
                      {new Date(p.created_at).toLocaleString()}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right Panel — Detail + Actions */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
        {!selected ? (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', paddingTop: 40 }}>
            Select a proposal to view details
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 900 }}>
            {/* Header */}
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

            {/* Meta */}
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: '0.8rem', color: 'var(--text-dim)' }}>
              <span>Model: {selected.llm_model || '?'}</span>
              <span>Eval: {selected.eval_overall}/5</span>
              <span>Priority: {selected.priority_score.toFixed(3)}</span>
              <span>Query: #{selected.query_id}</span>
              {selected.prompt_hash && <span title={selected.prompt_hash}>Hash: {selected.prompt_hash.slice(0, 8)}...</span>}
            </div>

            {/* Gap Evidence */}
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

            {/* LLM Reasoning */}
            {selected.llm_reasoning && (
              <Section title="LLM Reasoning">
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem', margin: 0, color: 'var(--text)' }}>
                  {selected.llm_reasoning}
                </pre>
              </Section>
            )}

            {/* Eval Context */}
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

            {/* Proposed Changes */}
            <Section title={`Proposed Changes (${selected.items.length})`}>
              {selected.items.map(item => (
                <ItemCard key={item.id} item={item} />
              ))}
            </Section>

            {/* Review Info (if reviewed) */}
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

            {/* Application Info */}
            {selected.applied_by && (
              <Section title="Application">
                <div style={{ fontSize: '0.8rem' }}>
                  Applied by {selected.applied_by}
                  {selected.applied_at && <> on {new Date(selected.applied_at).toLocaleString()}</>}
                </div>
              </Section>
            )}

            {/* Actions — inline in the detail flow */}
            {selected.state === 'proposed' && (
              <Section title="Review Decision">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input
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
                      Approve
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
                      Reject
                    </button>
                  </div>
                </div>
              </Section>
            )}

            {selected.state === 'approved' && (
              <Section title="Apply to Graph">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input
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
                    Apply to Graph
                  </button>
                </div>
              </Section>
            )}
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


function ItemCard({ item }: { item: ProposalItem }) {
  const spec = item.neuron_spec_json ? JSON.parse(item.neuron_spec_json) : null;

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
        {item.target_neuron_id && <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>Neuron #{item.target_neuron_id}</span>}
        {item.created_neuron_id && <span style={{ color: '#4caf50', fontSize: '0.75rem' }}>Created #{item.created_neuron_id}</span>}
      </div>

      {item.action === 'update' && (
        <>
          <div><strong>Field:</strong> {item.field}</div>
          {item.old_value && (
            <div style={{ marginTop: 4 }}>
              <div style={{ color: '#e74c3c', fontSize: '0.75rem' }}>- {item.old_value.slice(0, 200)}{item.old_value.length > 200 ? '...' : ''}</div>
              <div style={{ color: '#4caf50', fontSize: '0.75rem' }}>+ {item.new_value?.slice(0, 200)}{(item.new_value?.length ?? 0) > 200 ? '...' : ''}</div>
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
            <div style={{ marginTop: 4, padding: 6, background: 'var(--bg-card)', borderRadius: 4, maxHeight: 100, overflow: 'auto' }}>
              {spec.content.slice(0, 300)}{spec.content.length > 300 ? '...' : ''}
            </div>
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
