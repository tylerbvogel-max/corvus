import { useState, useEffect, useCallback } from 'react';
import { DEPARTMENTS, DEPT_ROLES } from '../constants';
import { useModels } from '../hooks/useModels';
import { fetchEmergentQueue, dismissEmergentEntry, scanReferences, ingestSource, applyIngestSource, extractSourceFromFile, extractSourceFromUrl, startBatchIngest, pollBatchIngest, cancelBatchIngest, listBatchJobs, resumeBatchIngest, type EmergentQueueEntry, type ScanReferencesResponse, type IngestProposal, type IngestSourceResponse, type IngestApplyResponse, type BatchIngestStatusResponse, type BatchJobSummary } from '../api/operator';

type StatusFilter = 'all' | 'pending' | 'dismissed' | 'resolved';
type SortKey = 'detection_count' | 'last_detected_at' | 'citation_pattern' | 'family';

const SOURCE_TYPES = [
  { value: 'regulatory_primary', label: 'Regulatory Primary' },
  { value: 'regulatory_interpretive', label: 'Regulatory Interpretive' },
  { value: 'technical_primary', label: 'Technical Primary' },
  { value: 'technical_pattern', label: 'Technical Pattern' },
];

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
  borderRadius: 4, padding: '4px 8px', fontSize: '0.8rem',
};

const inputStyle: React.CSSProperties = {
  ...selectStyle, width: '100%', boxSizing: 'border-box' as const,
};

export default function EmergentQueuePage() {
  const { grouped: groupedModels } = useModels();
  const [entries, setEntries] = useState<EmergentQueueEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending');
  const [sortKey, setSortKey] = useState<SortKey>('detection_count');
  const [sortAsc, setSortAsc] = useState(false);
  const [domainFilter, setDomainFilter] = useState<string>('all');
  const [familyFilter, setFamilyFilter] = useState<string>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [dismissingId, setDismissingId] = useState<number | null>(null);
  const [dismissNotes, setDismissNotes] = useState('');
  const [showDismissModal, setShowDismissModal] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanReferencesResponse | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Acquire workflow state
  const [acquireEntry, setAcquireEntry] = useState<EmergentQueueEntry | null>(null);
  const [acquireForm, setAcquireForm] = useState({
    source_text: '',
    source_type: 'regulatory_primary',
    source_url: '',
    effective_date: '',
    department: '',
    role_key: '',
  });
  const [acquireStep, setAcquireStep] = useState<'input' | 'batch' | 'review' | 'done'>('input');
  const [acquireLoading, setAcquireLoading] = useState(false);
  const [proposals, setProposals] = useState<IngestProposal[]>([]);
  const [proposalSelection, setProposalSelection] = useState<Set<number>>(new Set());
  const [ingestMeta, setIngestMeta] = useState<IngestSourceResponse | null>(null);
  const [applyResult, setApplyResult] = useState<IngestApplyResponse | null>(null);
  const [sourceMode, setSourceMode] = useState<'paste' | 'file' | 'url'>('paste');
  const [extracting, setExtracting] = useState(false);
  const [extractInfo, setExtractInfo] = useState('');
  const [pageStart, setPageStart] = useState(1);
  const [pageEnd, setPageEnd] = useState<number | ''>('');
  const [batchJobId, setBatchJobId] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchIngestStatusResponse | null>(null);
  const [batchModel, setBatchModel] = useState<string>('haiku');
  const [activeJobs, setActiveJobs] = useState<BatchJobSummary[]>([]);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const statusParam = statusFilter === 'all' ? undefined : statusFilter;
      const data = await fetchEmergentQueue(statusParam);
      setEntries(data.entries);
      setTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load queue');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  // Poll for active batch jobs
  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const data = await listBatchJobs();
        if (!cancelled) setActiveJobs(data.jobs);
      } catch { /* ignore */ }
    }
    poll();
    const interval = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // Derived filter values
  const domains = [...new Set(entries.map(e => e.domain))].sort();
  const families = [...new Set(entries.map(e => e.family).filter(Boolean))].sort() as string[];

  // Filter and sort
  const filtered = entries
    .filter(e => domainFilter === 'all' || e.domain === domainFilter)
    .filter(e => familyFilter === 'all' || e.family === familyFilter)
    .filter(e => !searchTerm || e.citation_pattern.toLowerCase().includes(searchTerm.toLowerCase()))
    .sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'detection_count') cmp = a.detection_count - b.detection_count;
      else if (sortKey === 'last_detected_at') cmp = (a.last_detected_at || '').localeCompare(b.last_detected_at || '');
      else if (sortKey === 'citation_pattern') cmp = a.citation_pattern.localeCompare(b.citation_pattern);
      else if (sortKey === 'family') cmp = (a.family || '').localeCompare(b.family || '');
      return sortAsc ? cmp : -cmp;
    });

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  }

  function sortArrow(key: SortKey) {
    if (sortKey !== key) return '';
    return sortAsc ? ' \u25B2' : ' \u25BC';
  }

  async function handleDismiss(id: number) {
    try {
      setDismissingId(id);
      await dismissEmergentEntry(id, dismissNotes);
      setShowDismissModal(null);
      setDismissNotes('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dismiss failed');
    } finally {
      setDismissingId(null);
    }
  }

  async function handleBatchDismiss() {
    try {
      setDismissingId(-1);
      for (const id of selected) {
        await dismissEmergentEntry(id, 'Batch dismiss');
      }
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Batch dismiss failed');
    } finally {
      setDismissingId(null);
    }
  }

  async function handleScan() {
    try {
      setScanning(true);
      setScanResult(null);
      setError(null);
      const result = await scanReferences();
      setScanResult(result);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  }

  function toggleSelect(id: number) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === filtered.length) setSelected(new Set());
    else setSelected(new Set(filtered.map(e => e.id)));
  }

  // ── Acquire workflow ──

  function openAcquire(entry: EmergentQueueEntry) {
    setAcquireEntry(entry);
    setAcquireStep('input');
    setProposals([]);
    setProposalSelection(new Set());
    setIngestMeta(null);
    setApplyResult(null);
    setSourceMode('paste');
    setExtractInfo('');
    setPageStart(1);
    setPageEnd('');
    setAcquireForm({
      source_text: '',
      source_type: entry.domain === 'regulatory' ? 'regulatory_primary' : 'technical_primary',
      source_url: '',
      effective_date: '',
      department: '',
      role_key: '',
    });
  }

  function closeAcquire() {
    setAcquireEntry(null);
    setAcquireStep('input');
    setProposals([]);
    setApplyResult(null);
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setExtracting(true);
      setError(null);
      const result = await extractSourceFromFile(file, pageStart, pageEnd || undefined);
      setAcquireForm(f => ({ ...f, source_text: result.text }));
      setExtractInfo(result.source_info + ` — ${result.char_count.toLocaleString()} chars extracted`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extraction failed');
    } finally {
      setExtracting(false);
    }
  }

  async function handleUrlExtract() {
    if (!acquireForm.source_url.trim()) return;
    try {
      setExtracting(true);
      setError(null);
      const result = await extractSourceFromUrl(acquireForm.source_url, pageStart, pageEnd || undefined);
      setAcquireForm(f => ({ ...f, source_text: result.text }));
      setExtractInfo(result.source_info + ` — ${result.char_count.toLocaleString()} chars extracted`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extraction failed');
    } finally {
      setExtracting(false);
    }
  }

  const BATCH_THRESHOLD = 10000; // chars — above this, use batch mode

  async function handleGenerateProposals() {
    if (!acquireEntry || !acquireForm.source_text.trim()) return;

    const payload = {
      source_text: acquireForm.source_text,
      citation: acquireEntry.citation_pattern,
      source_type: acquireForm.source_type,
      source_url: acquireForm.source_url || undefined,
      effective_date: acquireForm.effective_date || undefined,
      department: acquireForm.department || undefined,
      role_key: acquireForm.role_key || undefined,
      queue_entry_id: acquireEntry.id,
    };

    if (acquireForm.source_text.length > BATCH_THRESHOLD) {
      // Batch mode — background processing
      try {
        setAcquireLoading(true);
        setError(null);
        setAcquireStep('batch');
        const result = await startBatchIngest({ ...payload, model: batchModel });
        setBatchJobId(result.job_id);
        // Start polling
        pollBatchJob(result.job_id);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to start batch ingestion');
        setAcquireStep('input');
        setAcquireLoading(false);
      }
    } else {
      // Single-shot mode (small text)
      try {
        setAcquireLoading(true);
        setError(null);
        const result = await ingestSource(payload);
        setProposals(result.proposals);
        setProposalSelection(new Set(result.proposals.map((_, i) => i)));
        setIngestMeta(result);
        setAcquireStep('review');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to generate proposals');
      } finally {
        setAcquireLoading(false);
      }
    }
  }

  function pollBatchJob(jobId: string) {
    const interval = setInterval(async () => {
      try {
        const status = await pollBatchIngest(jobId);
        setBatchStatus(status);
        if (status.status === 'done' || status.status === 'cancelled') {
          clearInterval(interval);
          if (status.status === 'done') {
            setProposals(status.proposals);
            setProposalSelection(new Set(status.proposals.map((_, i) => i)));
            setIngestMeta({
              proposals: status.proposals,
              count: status.proposals.length,
              citation: status.citation,
              source_type: acquireForm.source_type,
              department: status.department,
              role_key: status.role_key,
              parent_id: status.parent_id,
              parent_label: status.parent_label,
              queue_entry_id: status.queue_entry_id,
              llm_cost: {
                input_tokens: status.input_tokens,
                output_tokens: status.output_tokens,
                cost_usd: status.cost_usd,
              },
            });
            setAcquireStep('review');
          }
          setAcquireLoading(false);
        }
      } catch {
        // keep polling on transient errors
      }
    }, 3000);
    // Store interval so we can clear on cancel/close
    return interval;
  }

  async function handleCancelBatch() {
    if (batchJobId) {
      try {
        await cancelBatchIngest(batchJobId);
      } catch { /* ignore */ }
    }
    setBatchJobId(null);
    setBatchStatus(null);
    setAcquireStep('input');
    setAcquireLoading(false);
  }

  async function handleApplyProposals() {
    if (!acquireEntry) return;
    const approved = proposals.filter((_, i) => proposalSelection.has(i));
    if (!approved.length) return;
    try {
      setAcquireLoading(true);
      setError(null);
      const result = await applyIngestSource({
        proposals: approved,
        queue_entry_id: acquireEntry.id,
      });
      setApplyResult(result);
      setAcquireStep('done');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to apply proposals');
    } finally {
      setAcquireLoading(false);
    }
  }

  function toggleProposal(idx: number) {
    setProposalSelection(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  }

  const pendingCount = entries.filter(e => e.status === 'pending').length;
  const resolvedCount = entries.filter(e => e.status === 'resolved').length;
  const dismissedCount = entries.filter(e => e.status === 'dismissed').length;

  if (error && !entries.length && !acquireEntry) return <div className="error-msg">{error}</div>;

  return (
    <div className="security-page">
      <h2>Emergent Queue</h2>
      <p className="security-intro">
        Unresolved external references detected across the neuron graph. When neurons reference
        standards, regulations, or technical frameworks that don't have corresponding primary neurons,
        they appear here for resolution. Priority is ranked by detection count.
      </p>

      {/* Summary cards */}
      <div className="stat-cards" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <div className="card-value">{total}</div>
          <div className="card-label">Total Entries</div>
        </div>
        <div className="stat-card">
          <div className="card-value" style={{ color: pendingCount > 0 ? '#fb923c' : '#22c55e' }}>{pendingCount}</div>
          <div className="card-label">Pending</div>
        </div>
        <div className="stat-card">
          <div className="card-value" style={{ color: '#22c55e' }}>{resolvedCount}</div>
          <div className="card-label">Resolved</div>
        </div>
        <div className="stat-card">
          <div className="card-value" style={{ color: '#c8d0dc' }}>{dismissedCount}</div>
          <div className="card-label">Dismissed</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{families.length}</div>
          <div className="card-label">Citation Families</div>
        </div>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)} style={selectStyle}>
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="dismissed">Dismissed</option>
          <option value="resolved">Resolved</option>
        </select>

        <select value={domainFilter} onChange={e => setDomainFilter(e.target.value)} style={selectStyle}>
          <option value="all">All domains</option>
          {domains.map(d => <option key={d} value={d}>{d}</option>)}
        </select>

        <select value={familyFilter} onChange={e => setFamilyFilter(e.target.value)} style={selectStyle}>
          <option value="all">All families</option>
          {families.map(f => <option key={f} value={f}>{f}</option>)}
        </select>

        <input
          type="text"
          placeholder="Search citations..."
          value={searchTerm}
          onChange={e => setSearchTerm(e.target.value)}
          style={{ ...selectStyle, minWidth: 160 }}
        />

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {selected.size > 0 && statusFilter !== 'dismissed' && (
            <button
              onClick={handleBatchDismiss}
              disabled={dismissingId !== null}
              style={{
                background: '#ef444422', color: '#ef4444', border: '1px solid #ef444444',
                borderRadius: 4, padding: '4px 12px', fontSize: '0.8rem', cursor: 'pointer',
              }}
            >
              {dismissingId === -1 ? 'Dismissing...' : `Dismiss ${selected.size} selected`}
            </button>
          )}
          <button
            onClick={handleScan}
            disabled={scanning}
            style={{
              background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
              borderRadius: 4, padding: '4px 12px', fontSize: '0.8rem', cursor: 'pointer',
            }}
          >
            {scanning ? 'Scanning...' : 'Re-scan References'}
          </button>
        </div>
      </div>

      {/* Scan result banner */}
      {scanResult && (
        <div style={{
          background: '#22c55e11', border: '1px solid #22c55e33', borderRadius: 6,
          padding: '10px 14px', marginBottom: 16, fontSize: '0.8rem',
        }}>
          <strong>Scan complete:</strong>{' '}
          {scanResult.neurons_scanned} neurons scanned,{' '}
          {scanResult.total_references_found} references found,{' '}
          {scanResult.new_queue_entries} new queue entries,{' '}
          {scanResult.existing_queue_entries_incremented} existing entries incremented.
          {scanResult.top_unresolved_families.length > 0 && (
            <span style={{ marginLeft: 8 }}>
              Top unresolved: {scanResult.top_unresolved_families.map(f => `${f.family} (${f.count})`).join(', ')}
            </span>
          )}
          <button
            onClick={() => setScanResult(null)}
            style={{ marginLeft: 8, background: 'none', border: 'none', color: '#c8d0dc', cursor: 'pointer', fontSize: '0.8rem' }}
          >
            dismiss
          </button>
        </div>
      )}

      {/* Active batch jobs panel */}
      {activeJobs.length > 0 && (
        <div style={{
          background: '#1e293b', border: '1px solid var(--border)', borderRadius: 6,
          padding: '12px 16px', marginBottom: 16,
        }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            Batch Jobs
            {activeJobs.some(j => j.status === 'running') && (
              <span style={{
                display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                background: '#22c55e', animation: 'pulse 2s infinite',
              }} />
            )}
          </div>
          {activeJobs.map(job => {
            const statusColor = job.status === 'running' ? '#fb923c'
              : job.status === 'done' ? '#22c55e'
              : job.status === 'interrupted' ? '#f59e0b'
              : job.status === 'error' ? '#ef4444'
              : '#c8d0dc';
            const canResume = job.status === 'interrupted' || job.status === 'error';
            return (
              <div key={job.job_id} style={{
                display: 'flex', alignItems: 'center', gap: 12, fontSize: '0.8rem',
                padding: '6px 0', borderTop: '1px solid var(--border)',
                background: canResume ? '#f59e0b08' : undefined,
              }}>
                <span style={{
                  color: statusColor, fontWeight: 600, minWidth: 80,
                  fontSize: '0.7rem', textTransform: 'uppercase',
                }}>
                  {job.status}
                </span>
                <span style={{ color: 'var(--text)' }}>{job.citation}</span>
                {(job.status === 'running' || canResume) && (
                  <>
                    <span style={{ color: '#c8d0dc' }}>
                      chunk {job.current_chunk}/{job.total_chunks}
                    </span>
                    <div style={{
                      flex: 1, maxWidth: 120, height: 6, background: '#334155', borderRadius: 3, overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${job.total_chunks ? (job.current_chunk / job.total_chunks) * 100 : 0}%`,
                        height: '100%', background: canResume ? statusColor : '#fb923c', borderRadius: 3,
                        transition: 'width 0.5s ease',
                      }} />
                    </div>
                  </>
                )}
                <span style={{ color: '#c8d0dc' }}>{job.proposals_count} proposals</span>
                <span style={{ color: '#c8d0dc' }}>${job.cost_usd.toFixed(4)}</span>
                {job.errors.length > 0 && (
                  <span style={{ color: '#ef4444' }}>{job.errors.length} errors</span>
                )}
                {canResume && (
                  <button
                    onClick={async () => {
                      try {
                        await resumeBatchIngest(job.job_id);
                      } catch (e) {
                        setError(e instanceof Error ? e.message : 'Resume failed');
                      }
                    }}
                    style={{
                      background: '#22c55e22', border: '1px solid #22c55e44', borderRadius: 3,
                      color: '#22c55e', fontSize: '0.7rem', padding: '2px 10px', cursor: 'pointer',
                      fontWeight: 600, marginLeft: 'auto',
                    }}
                  >
                    Resume
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {error && (
        <div style={{ color: '#ef4444', fontSize: '0.8rem', marginBottom: 8 }}>{error}</div>
      )}

      {loading ? (
        <div className="loading">Loading emergent queue...</div>
      ) : filtered.length === 0 ? (
        <div style={{ color: '#c8d0dc', fontSize: '0.85rem', padding: 16, textAlign: 'center' }}>
          {entries.length === 0 ? 'No entries in queue. Run a reference scan to detect unresolved citations.' : 'No entries match current filters.'}
        </div>
      ) : (
        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr>
              <th style={{ width: 30 }}>
                <input type="checkbox" checked={selected.size === filtered.length && filtered.length > 0} onChange={toggleSelectAll} />
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('citation_pattern')}>
                Citation{sortArrow('citation_pattern')}
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('family')}>
                Family{sortArrow('family')}
              </th>
              <th>Domain</th>
              <th style={{ cursor: 'pointer', textAlign: 'right' }} onClick={() => handleSort('detection_count')}>
                Detections{sortArrow('detection_count')}
              </th>
              <th>Neurons</th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('last_detected_at')}>
                Last Seen{sortArrow('last_detected_at')}
              </th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(entry => (
              <tr key={entry.id} style={{ opacity: entry.status === 'dismissed' ? 0.5 : 1 }}>
                <td>
                  <input type="checkbox" checked={selected.has(entry.id)} onChange={() => toggleSelect(entry.id)} />
                </td>
                <td>
                  <code style={{ fontSize: '0.75rem' }}>{entry.citation_pattern}</code>
                  {entry.notes && (
                    <div style={{ fontSize: '0.7rem', color: '#c8d0dc', marginTop: 2 }}>{entry.notes}</div>
                  )}
                </td>
                <td>
                  <span style={{
                    fontSize: '0.7rem', padding: '1px 6px', borderRadius: 3,
                    background: entry.domain === 'regulatory' ? '#60a5fa22' : '#a78bfa22',
                    color: entry.domain === 'regulatory' ? '#60a5fa' : '#a78bfa',
                    border: `1px solid ${entry.domain === 'regulatory' ? '#60a5fa44' : '#a78bfa44'}`,
                  }}>
                    {entry.family || 'unknown'}
                  </span>
                </td>
                <td style={{ color: '#c8d0dc', fontSize: '0.75rem' }}>{entry.domain}</td>
                <td style={{ textAlign: 'right', fontWeight: 600 }}>
                  <span style={{ color: entry.detection_count >= 10 ? '#ef4444' : entry.detection_count >= 5 ? '#fb923c' : 'var(--text)' }}>
                    {entry.detection_count}
                  </span>
                </td>
                <td style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>
                  {entry.detected_in_neuron_ids.length} neuron{entry.detected_in_neuron_ids.length !== 1 ? 's' : ''}
                </td>
                <td style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>
                  {entry.last_detected_at ? entry.last_detected_at.split('T')[0] : '\u2014'}
                </td>
                <td>
                  <span style={{
                    fontSize: '0.65rem', padding: '1px 6px', borderRadius: 3, textTransform: 'uppercase', fontWeight: 600,
                    background: entry.status === 'pending' ? '#fb923c22' : entry.status === 'resolved' ? '#22c55e22' : '#c8d0dc22',
                    color: entry.status === 'pending' ? '#fb923c' : entry.status === 'resolved' ? '#22c55e' : '#c8d0dc',
                    border: `1px solid ${entry.status === 'pending' ? '#fb923c44' : entry.status === 'resolved' ? '#22c55e44' : '#c8d0dc44'}`,
                  }}>
                    {entry.status}
                  </span>
                </td>
                <td style={{ display: 'flex', gap: 4 }}>
                  {entry.status === 'pending' && (
                    <>
                      <button
                        onClick={() => openAcquire(entry)}
                        style={{
                          background: '#22c55e22', border: '1px solid #22c55e44', borderRadius: 3,
                          color: '#22c55e', fontSize: '0.7rem', padding: '2px 8px', cursor: 'pointer',
                        }}
                      >
                        Acquire
                      </button>
                      <button
                        onClick={() => setShowDismissModal(entry.id)}
                        disabled={dismissingId !== null}
                        style={{
                          background: 'none', border: '1px solid var(--border)', borderRadius: 3,
                          color: '#c8d0dc', fontSize: '0.7rem', padding: '2px 8px', cursor: 'pointer',
                        }}
                      >
                        Dismiss
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ fontSize: '0.75rem', color: '#c8d0dc', marginTop: 8 }}>
        Showing {filtered.length} of {total} entries
      </div>

      {/* ── Dismiss modal ── */}
      {showDismissModal !== null && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }} onClick={() => { setShowDismissModal(null); setDismissNotes(''); }}>
          <div
            style={{
              background: 'var(--bg-card, #1e1e2e)', border: '1px solid var(--border)', borderRadius: 8,
              padding: 20, minWidth: 360, maxWidth: 480,
            }}
            onClick={e => e.stopPropagation()}
          >
            <h4 style={{ margin: '0 0 12px', color: 'var(--text)' }}>Dismiss Entry</h4>
            <p style={{ fontSize: '0.8rem', color: '#c8d0dc', margin: '0 0 12px' }}>
              Citation: <code>{entries.find(e => e.id === showDismissModal)?.citation_pattern}</code>
            </p>
            <textarea
              placeholder="Reason for dismissal (optional)"
              value={dismissNotes}
              onChange={e => setDismissNotes(e.target.value)}
              rows={3}
              style={{
                width: '100%', background: 'var(--bg-input)', color: 'var(--text)',
                border: '1px solid var(--border)', borderRadius: 4, padding: 8,
                fontSize: '0.8rem', resize: 'vertical', boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button
                onClick={() => { setShowDismissModal(null); setDismissNotes(''); }}
                style={{
                  background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                  color: '#c8d0dc', padding: '6px 14px', fontSize: '0.8rem', cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleDismiss(showDismissModal)}
                disabled={dismissingId !== null}
                style={{
                  background: '#ef444422', border: '1px solid #ef444444', borderRadius: 4,
                  color: '#ef4444', padding: '6px 14px', fontSize: '0.8rem', cursor: 'pointer',
                }}
              >
                {dismissingId === showDismissModal ? 'Dismissing...' : 'Dismiss'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Acquire modal ── */}
      {acquireEntry && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }} onClick={closeAcquire}>
          <div
            style={{
              background: 'var(--bg-card, #1e1e2e)', border: '1px solid var(--border)', borderRadius: 8,
              padding: 24, minWidth: 500, maxWidth: 800, maxHeight: '85vh', overflowY: 'auto',
              width: '90vw',
            }}
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0, color: 'var(--text)' }}>Acquire Source</h3>
                <div style={{ fontSize: '0.8rem', color: '#c8d0dc', marginTop: 4 }}>
                  <code>{acquireEntry.citation_pattern}</code>
                  <span style={{
                    marginLeft: 8, fontSize: '0.7rem', padding: '1px 6px', borderRadius: 3,
                    background: acquireEntry.domain === 'regulatory' ? '#60a5fa22' : '#a78bfa22',
                    color: acquireEntry.domain === 'regulatory' ? '#60a5fa' : '#a78bfa',
                  }}>
                    {acquireEntry.family}
                  </span>
                  <span style={{ marginLeft: 8 }}>
                    Referenced by {acquireEntry.detected_in_neuron_ids.length} neuron{acquireEntry.detected_in_neuron_ids.length !== 1 ? 's' : ''}
                  </span>
                </div>
              </div>
              {/* Step indicator */}
              <div style={{ display: 'flex', gap: 4, fontSize: '0.7rem' }}>
                {(['input', 'batch', 'review', 'done'] as const).map((step, i) => {
                  const labels = { input: 'Source', batch: 'Processing', review: 'Review', done: 'Done' };
                  if (step === 'batch' && acquireStep !== 'batch') return null;
                  return (
                    <span key={step} style={{
                      padding: '2px 8px', borderRadius: 3,
                      background: acquireStep === step ? '#60a5fa33' : 'var(--bg-input)',
                      color: acquireStep === step ? '#60a5fa' : '#c8d0dc',
                      fontWeight: acquireStep === step ? 600 : 400,
                    }}>
                      {i + 1}. {labels[step]}
                    </span>
                  );
                })}
              </div>
            </div>

            {/* Step 1: Input source text + metadata */}
            {acquireStep === 'input' && (
              <div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                  <label style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: '#c8d0dc', display: 'block', marginBottom: 4 }}>Source Type</span>
                    <select
                      value={acquireForm.source_type}
                      onChange={e => setAcquireForm(f => ({ ...f, source_type: e.target.value }))}
                      style={inputStyle}
                    >
                      {SOURCE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </label>
                  <label style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: '#c8d0dc', display: 'block', marginBottom: 4 }}>Source URL (optional)</span>
                    <input
                      type="text"
                      placeholder="https://..."
                      value={acquireForm.source_url}
                      onChange={e => setAcquireForm(f => ({ ...f, source_url: e.target.value }))}
                      style={inputStyle}
                    />
                  </label>
                  <label style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: '#c8d0dc', display: 'block', marginBottom: 4 }}>Target Department <span style={{ color: '#ef4444' }}>*</span></span>
                    <select
                      value={acquireForm.department}
                      onChange={e => setAcquireForm(f => ({ ...f, department: e.target.value, role_key: '' }))}
                      style={inputStyle}
                    >
                      <option value="">Select department...</option>
                      {DEPARTMENTS.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </label>
                  <label style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: '#c8d0dc', display: 'block', marginBottom: 4 }}>Target Role</span>
                    <select
                      value={acquireForm.role_key}
                      onChange={e => setAcquireForm(f => ({ ...f, role_key: e.target.value }))}
                      style={inputStyle}
                      disabled={!acquireForm.department}
                    >
                      <option value="">Select role (optional)...</option>
                      {(DEPT_ROLES[acquireForm.department] || []).map(r => (
                        <option key={r.key} value={r.key}>{r.label}</option>
                      ))}
                    </select>
                  </label>
                  <label style={{ fontSize: '0.8rem' }}>
                    <span style={{ color: '#c8d0dc', display: 'block', marginBottom: 4 }}>Effective Date (optional)</span>
                    <input
                      type="date"
                      value={acquireForm.effective_date}
                      onChange={e => setAcquireForm(f => ({ ...f, effective_date: e.target.value }))}
                      style={inputStyle}
                    />
                  </label>
                </div>

                {/* Source input mode tabs */}
                <div style={{ marginBottom: 8 }}>
                  <span style={{ color: '#c8d0dc', fontSize: '0.8rem', display: 'block', marginBottom: 6 }}>
                    Source Input <span style={{ color: '#ef4444' }}>*</span>
                  </span>
                  <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                    {([['paste', 'Paste Text'], ['file', 'Upload File'], ['url', 'Fetch URL']] as const).map(([mode, label]) => (
                      <button
                        key={mode}
                        onClick={() => setSourceMode(mode)}
                        style={{
                          background: sourceMode === mode ? '#60a5fa22' : 'var(--bg-input)',
                          color: sourceMode === mode ? '#60a5fa' : '#c8d0dc',
                          border: `1px solid ${sourceMode === mode ? '#60a5fa44' : 'var(--border)'}`,
                          borderRadius: 4, padding: '4px 12px', fontSize: '0.75rem', cursor: 'pointer',
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>

                  {sourceMode === 'file' && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                        <input
                          type="file"
                          accept=".pdf,.txt,.md"
                          onChange={handleFileUpload}
                          disabled={extracting}
                          style={{ fontSize: '0.8rem', color: 'var(--text)' }}
                        />
                        {extracting && <span style={{ fontSize: '0.75rem', color: '#fb923c' }}>Extracting...</span>}
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <label style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>
                          Pages: <input type="number" min={1} value={pageStart} onChange={e => setPageStart(Number(e.target.value) || 1)}
                            style={{ ...inputStyle, width: 60 }} /> to <input type="number" min={1} value={pageEnd} onChange={e => setPageEnd(e.target.value ? Number(e.target.value) : '')}
                            placeholder="end" style={{ ...inputStyle, width: 60 }} />
                        </label>
                        <span style={{ fontSize: '0.7rem', color: '#c8d0dc' }}>(leave end blank for all pages)</span>
                      </div>
                    </div>
                  )}

                  {sourceMode === 'url' && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                        <input
                          type="text"
                          placeholder="https://example.com/document.pdf"
                          value={acquireForm.source_url}
                          onChange={e => setAcquireForm(f => ({ ...f, source_url: e.target.value }))}
                          style={{ ...inputStyle, flex: 1 }}
                        />
                        <button
                          onClick={handleUrlExtract}
                          disabled={extracting || !acquireForm.source_url.trim()}
                          style={{
                            background: '#60a5fa22', border: '1px solid #60a5fa44', borderRadius: 4,
                            color: '#60a5fa', padding: '4px 12px', fontSize: '0.8rem', cursor: 'pointer',
                            opacity: extracting || !acquireForm.source_url.trim() ? 0.5 : 1,
                          }}
                        >
                          {extracting ? 'Fetching...' : 'Fetch'}
                        </button>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <label style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>
                          Pages (PDF only): <input type="number" min={1} value={pageStart} onChange={e => setPageStart(Number(e.target.value) || 1)}
                            style={{ ...inputStyle, width: 60 }} /> to <input type="number" min={1} value={pageEnd} onChange={e => setPageEnd(e.target.value ? Number(e.target.value) : '')}
                            placeholder="end" style={{ ...inputStyle, width: 60 }} />
                        </label>
                      </div>
                    </div>
                  )}

                  {extractInfo && (
                    <div style={{ fontSize: '0.7rem', color: '#22c55e', marginBottom: 6 }}>{extractInfo}</div>
                  )}

                  <textarea
                    placeholder={sourceMode === 'paste' ? 'Paste the source material here. The LLM will segment this into neuron proposals...' : 'Extracted text will appear here. You can edit before generating proposals.'}
                    value={acquireForm.source_text}
                    onChange={e => setAcquireForm(f => ({ ...f, source_text: e.target.value }))}
                    rows={10}
                    style={{
                      ...inputStyle, resize: 'vertical', fontFamily: 'monospace', fontSize: '0.75rem',
                      lineHeight: 1.5,
                    }}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
                  <span style={{ fontSize: '0.7rem', color: '#c8d0dc' }}>
                    {acquireForm.source_text.length.toLocaleString()} characters
                    {acquireForm.source_text.length > BATCH_THRESHOLD && (
                      <span style={{ color: '#fb923c' }}> — batch mode (chunked processing)</span>
                    )}
                  </span>
                  {acquireForm.source_text.length > BATCH_THRESHOLD && (
                    <label style={{ fontSize: '0.75rem', color: '#c8d0dc', display: 'flex', alignItems: 'center', gap: 4 }}>
                      Model:
                      <select
                        value={batchModel}
                        onChange={e => setBatchModel(e.target.value)}
                        style={{ ...selectStyle, fontSize: '0.75rem' }}
                      >
                        {Object.entries(groupedModels).map(([group, models]) => (
                          [
                            <option key={`hdr-${group}`} disabled style={{ fontWeight: 'bold' }}>── {group} ──</option>,
                            ...models.map(m => (
                              <option key={m.display_name} value={m.display_name}>
                                {m.display_name}
                              </option>
                            )),
                          ]
                        ))}
                      </select>
                    </label>
                  )}
                </div>

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                  <button onClick={closeAcquire} style={{
                    background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                    color: '#c8d0dc', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                  }}>
                    Cancel
                  </button>
                  <button
                    onClick={handleGenerateProposals}
                    disabled={acquireLoading || !acquireForm.source_text.trim() || !acquireForm.department}
                    style={{
                      background: '#60a5fa22', border: '1px solid #60a5fa44', borderRadius: 4,
                      color: '#60a5fa', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                      opacity: acquireLoading || !acquireForm.source_text.trim() || !acquireForm.department ? 0.5 : 1,
                    }}
                  >
                    {acquireLoading ? 'Generating proposals...' : 'Generate Neuron Proposals'}
                  </button>
                </div>
              </div>
            )}

            {/* Batch processing step */}
            {acquireStep === 'batch' && (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <div style={{ fontSize: '1.2rem', color: '#60a5fa', marginBottom: 16 }}>
                  Processing document...
                </div>
                {batchStatus && (
                  <>
                    <div style={{
                      width: '100%', height: 8, background: 'var(--bg-input)',
                      borderRadius: 4, overflow: 'hidden', marginBottom: 12,
                    }}>
                      <div style={{
                        width: `${batchStatus.total_chunks > 0 ? (batchStatus.current_chunk / batchStatus.total_chunks) * 100 : 0}%`,
                        height: '100%', background: '#60a5fa', borderRadius: 4,
                        transition: 'width 0.5s ease',
                      }} />
                    </div>
                    <div style={{ fontSize: '0.85rem', color: 'var(--text)', marginBottom: 8 }}>
                      {batchStatus.step}
                    </div>
                    <div style={{ display: 'flex', gap: 24, justifyContent: 'center', fontSize: '0.8rem', color: '#c8d0dc' }}>
                      <span>Chunk {batchStatus.current_chunk} / {batchStatus.total_chunks}</span>
                      <span>{batchStatus.proposals_count} proposals so far</span>
                      <span>${batchStatus.cost_usd.toFixed(4)}</span>
                    </div>
                    {batchStatus.errors.length > 0 && (
                      <div style={{ marginTop: 12, fontSize: '0.75rem', color: '#fb923c', textAlign: 'left' }}>
                        {batchStatus.errors.map((e, i) => <div key={i}>{e}</div>)}
                      </div>
                    )}
                  </>
                )}
                <button
                  onClick={handleCancelBatch}
                  style={{
                    marginTop: 20, background: '#ef444422', border: '1px solid #ef444444',
                    borderRadius: 4, color: '#ef4444', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Step 2: Review proposals */}
            {acquireStep === 'review' && (
              <div>
                {ingestMeta && (
                  <div style={{
                    background: 'var(--bg-input)', borderRadius: 6, padding: '8px 12px',
                    marginBottom: 16, fontSize: '0.75rem', display: 'flex', gap: 16,
                  }}>
                    <span><strong>{proposals.length}</strong> proposals generated</span>
                    {ingestMeta.parent_label && (
                      <span>Parent: <code>{ingestMeta.parent_label}</code></span>
                    )}
                    <span>Cost: ${ingestMeta.llm_cost.cost_usd.toFixed(4)}</span>
                    <span style={{ marginLeft: 'auto' }}>
                      <strong>{proposalSelection.size}</strong> of {proposals.length} selected
                    </span>
                  </div>
                )}

                {proposals.map((p, i) => (
                  <div
                    key={i}
                    style={{
                      border: `1px solid ${proposalSelection.has(i) ? '#22c55e44' : 'var(--border)'}`,
                      borderRadius: 6, padding: 12, marginBottom: 8,
                      background: proposalSelection.has(i) ? '#22c55e08' : 'transparent',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                      <input
                        type="checkbox"
                        checked={proposalSelection.has(i)}
                        onChange={() => toggleProposal(i)}
                        style={{ marginTop: 3 }}
                      />
                      <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <strong style={{ fontSize: '0.85rem' }}>{p.label}</strong>
                          <span style={{
                            fontSize: '0.65rem', padding: '1px 6px', borderRadius: 3,
                            background: 'var(--bg-input)', color: '#c8d0dc',
                          }}>
                            L{p.layer} {p.node_type}
                          </span>
                        </div>
                        <div style={{ fontSize: '0.75rem', color: '#c8d0dc', marginBottom: 6 }}>
                          {p.summary}
                        </div>
                        <div style={{
                          fontSize: '0.75rem', color: 'var(--text)', lineHeight: 1.5,
                          background: 'var(--bg-input)', borderRadius: 4, padding: 8,
                          maxHeight: 120, overflow: 'auto',
                        }}>
                          {p.content}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: '#c8d0dc', marginTop: 4, fontStyle: 'italic' }}>
                          {p.reason}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                  <button onClick={() => setAcquireStep('input')} style={{
                    background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                    color: '#c8d0dc', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                  }}>
                    Back
                  </button>
                  <button onClick={closeAcquire} style={{
                    background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                    color: '#c8d0dc', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                  }}>
                    Cancel
                  </button>
                  <button
                    onClick={handleApplyProposals}
                    disabled={acquireLoading || proposalSelection.size === 0}
                    style={{
                      background: '#22c55e22', border: '1px solid #22c55e44', borderRadius: 4,
                      color: '#22c55e', padding: '8px 16px', fontSize: '0.8rem', cursor: 'pointer',
                      opacity: acquireLoading || proposalSelection.size === 0 ? 0.5 : 1,
                    }}
                  >
                    {acquireLoading ? 'Creating neurons...' : `Create ${proposalSelection.size} Neuron${proposalSelection.size !== 1 ? 's' : ''}`}
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Done */}
            {acquireStep === 'done' && applyResult && (
              <div style={{ textAlign: 'center', padding: '24px 0' }}>
                <div style={{ fontSize: '2rem', color: '#22c55e', marginBottom: 12 }}>
                  {applyResult.neurons_created} neuron{applyResult.neurons_created !== 1 ? 's' : ''} created
                </div>
                <div style={{ fontSize: '0.85rem', color: '#c8d0dc', marginBottom: 8 }}>
                  IDs: {applyResult.neuron_ids.join(', ')}
                </div>
                {applyResult.edges_created > 0 && (
                  <div style={{ fontSize: '0.8rem', color: '#60a5fa', marginBottom: 8 }}>
                    {applyResult.edges_created} co-firing edge{applyResult.edges_created !== 1 ? 's' : ''} created
                    to referencing neurons
                  </div>
                )}
                {applyResult.queue_entry_resolved && (
                  <div style={{ fontSize: '0.8rem', color: '#22c55e' }}>
                    Queue entry resolved
                  </div>
                )}
                <button onClick={closeAcquire} style={{
                  background: '#22c55e22', border: '1px solid #22c55e44', borderRadius: 4,
                  color: '#22c55e', padding: '8px 20px', fontSize: '0.85rem', cursor: 'pointer',
                  marginTop: 16,
                }}>
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
