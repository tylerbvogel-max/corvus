import { useState, useEffect, useRef, useCallback } from 'react';
import {
  uploadDocument,
  fetchDocumentJobs,
  fetchDocumentJobStatus,
  fetchDocumentStructure,
  cancelDocumentJob,
} from '../api';
import type { DocumentIngestJob, DocumentStructure } from '../api';

type ViewMode = 'list' | 'detail';

const STATUS_COLORS: Record<string, string> = {
  uploading: '#f59e0b',
  analyzing: '#3b82f6',
  extracting: '#8b5cf6',
  proposing: '#6366f1',
  done: '#22c55e',
  error: '#ef4444',
  cancelled: '#6b7280',
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: '0.8em',
      fontWeight: 600,
      background: (STATUS_COLORS[status] || '#666') + '22',
      color: STATUS_COLORS[status] || '#666',
      border: `1px solid ${STATUS_COLORS[status] || '#666'}44`,
    }}>
      {status}
    </span>
  );
}

function ProgressBar({ current, total }: { current: number; total: number }) {
  const pct = total > 0 ? (current / total) * 100 : 0;
  return (
    <div style={{
      width: '100%', height: 6, borderRadius: 3,
      background: 'var(--border, #333)', overflow: 'hidden',
    }}>
      <div style={{
        width: `${pct}%`, height: '100%', borderRadius: 3,
        background: 'var(--accent, #c87533)',
        transition: 'width 0.3s ease',
      }} />
    </div>
  );
}

// ── Upload Form ──

function UploadForm({ onUploaded }: { onUploaded: (job: DocumentIngestJob) => void }) {
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [sourceType, setSourceType] = useState('operational');
  const [authorityLevel, setAuthorityLevel] = useState('guidance');
  const [citation, setCitation] = useState('');
  const [department, setDepartment] = useState('');
  const [roleKey, setRoleKey] = useState('');
  const [model, setModel] = useState('sonnet');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  }, []);

  const handleSubmit = async () => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const job = await uploadDocument(file, {
        title, source_type: sourceType, authority_level: authorityLevel,
        citation, department, role_key: roleKey, model,
      });
      onUploaded(job);
      setFile(null);
      setTitle('');
      setCitation('');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="doc-upload-section">
      <h3 style={{ margin: '0 0 12px', fontSize: '1.05em' }}>Upload Document</h3>

      <div
        className={`doc-dropzone ${dragging ? 'doc-dropzone-active' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.doc,.html,.htm,.txt"
          style={{ display: 'none' }}
          onChange={e => { if (e.target.files?.[0]) setFile(e.target.files[0]); }}
        />
        {file ? (
          <div>
            <strong>{file.name}</strong>
            <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>
              ({formatBytes(file.size)})
            </span>
          </div>
        ) : (
          <div style={{ color: 'var(--text-dim)' }}>
            Drop a file here or click to browse (PDF, DOCX, HTML, TXT)
          </div>
        )}
      </div>

      <div className="doc-form-grid">
        <label className="doc-form-label">
          Title
          <input
            className="doc-form-input"
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Auto-detected from document if blank"
          />
        </label>
        <label className="doc-form-label">
          Citation
          <input
            className="doc-form-input"
            value={citation}
            onChange={e => setCitation(e.target.value)}
            placeholder="e.g. AS9100 Rev D, Section 8.5"
          />
        </label>
        <label className="doc-form-label">
          Source Type
          <select className="doc-form-input" value={sourceType} onChange={e => setSourceType(e.target.value)}>
            <option value="operational">Operational</option>
            <option value="regulatory_primary">Regulatory (Primary)</option>
            <option value="regulatory_guidance">Regulatory (Guidance)</option>
            <option value="industry_standard">Industry Standard</option>
            <option value="best_practice">Best Practice</option>
            <option value="internal_procedure">Internal Procedure</option>
          </select>
        </label>
        <label className="doc-form-label">
          Authority Level
          <select className="doc-form-input" value={authorityLevel} onChange={e => setAuthorityLevel(e.target.value)}>
            <option value="mandatory">Mandatory</option>
            <option value="advisory">Advisory</option>
            <option value="guidance">Guidance</option>
            <option value="reference">Reference</option>
          </select>
        </label>
        <label className="doc-form-label">
          Department
          <input
            className="doc-form-input"
            value={department}
            onChange={e => setDepartment(e.target.value)}
            placeholder="Target department (optional)"
          />
        </label>
        <label className="doc-form-label">
          Role
          <input
            className="doc-form-input"
            value={roleKey}
            onChange={e => setRoleKey(e.target.value)}
            placeholder="Target role key (optional)"
          />
        </label>
        <label className="doc-form-label">
          Model
          <select className="doc-form-input" value={model} onChange={e => setModel(e.target.value)}>
            <option value="sonnet">Sonnet (recommended)</option>
            <option value="haiku">Haiku (faster, cheaper)</option>
            <option value="opus">Opus (highest quality)</option>
            <option value="gemini-flash">Gemini Flash (free)</option>
          </select>
        </label>
      </div>

      {error && <div className="doc-error">{error}</div>}

      <button
        className="doc-upload-btn"
        onClick={handleSubmit}
        disabled={!file || uploading}
      >
        {uploading ? 'Uploading...' : 'Upload & Process'}
      </button>
    </div>
  );
}

// ── Job List ──

function JobList({
  jobs,
  onSelect,
  onRefresh,
}: {
  jobs: DocumentIngestJob[];
  onSelect: (job: DocumentIngestJob) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="doc-job-list">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: '1.05em' }}>Ingest Jobs</h3>
        <button className="doc-refresh-btn" onClick={onRefresh}>Refresh</button>
      </div>

      {jobs.length === 0 && (
        <div style={{ color: 'var(--text-dim)', padding: '16px 0', textAlign: 'center' }}>
          No document ingest jobs yet.
        </div>
      )}

      {jobs.map(job => (
        <div
          key={job.id}
          className="doc-job-card"
          onClick={() => onSelect(job)}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600 }}>{job.title || job.filename}</span>
            <StatusBadge status={job.status} />
          </div>
          <div style={{ fontSize: '0.85em', color: 'var(--text-dim)', marginTop: 4 }}>
            {job.filename} ({formatBytes(job.file_size_bytes)})
            {job.total_pages != null && ` - ${job.total_pages} pages`}
          </div>
          {(job.status === 'extracting' || job.status === 'analyzing') && (
            <div style={{ marginTop: 6 }}>
              {job.total_sections > 0 ? (
                <ProgressBar current={job.current_section} total={job.total_sections} />
              ) : (
                <div style={{
                  height: 6, background: 'var(--border)', borderRadius: 3,
                  overflow: 'hidden', position: 'relative',
                }}>
                  <div style={{
                    position: 'absolute', inset: 0,
                    background: 'linear-gradient(90deg, transparent, #8b5cf6, transparent)',
                    animation: 'doc-indet 1.5s linear infinite',
                  }} />
                </div>
              )}
              <div style={{ fontSize: '0.8em', color: 'var(--text-dim)', marginTop: 2 }}>
                {job.step}
              </div>
            </div>
          )}
          {job.status === 'done' && (
            <div style={{ fontSize: '0.85em', color: 'var(--text-dim)', marginTop: 4 }}>
              {job.proposal_ids.length} proposals | ${job.cost_usd.toFixed(4)} |
              {job.duplicates_flagged > 0 && ` ${job.duplicates_flagged} duplicates flagged |`}
              {' '}{job.total_sections} sections
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Job Detail ──

function JobDetail({
  jobId,
  onBack,
}: {
  jobId: string;
  onBack: () => void;
}) {
  const [job, setJob] = useState<DocumentIngestJob | null>(null);
  const [structure, setStructure] = useState<DocumentStructure | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadJob = useCallback(async () => {
    try {
      const j = await fetchDocumentJobStatus(jobId);
      setJob(j);
      if (j.status === 'done' || j.status === 'error' || j.status === 'cancelled') {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    } catch {
      // ignore polling errors
    }
  }, [jobId]);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchDocumentJobStatus(jobId),
      fetchDocumentStructure(jobId).catch(() => null),
    ]).then(([j, s]) => {
      setJob(j);
      if (s) setStructure(s);
      setLoading(false);
    });

    pollRef.current = setInterval(loadJob, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId, loadJob]);

  const handleCancel = async () => {
    if (!job) return;
    try {
      const updated = await cancelDocumentJob(job.id);
      setJob(updated);
    } catch {
      // ignore
    }
  };

  if (loading || !job) {
    return <div style={{ padding: 16, color: 'var(--text-dim)' }}>Loading...</div>;
  }

  return (
    <div className="doc-detail">
      <button className="doc-back-btn" onClick={onBack}>Back to Jobs</button>

      <div className="doc-detail-header">
        <h3 style={{ margin: '0 0 4px' }}>{job.title || job.filename}</h3>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <StatusBadge status={job.status} />
          <span style={{ fontSize: '0.85em', color: 'var(--text-dim)' }}>
            {job.filename} ({formatBytes(job.file_size_bytes)})
          </span>
          {job.total_pages != null && (
            <span style={{ fontSize: '0.85em', color: 'var(--text-dim)' }}>
              {job.total_pages} pages
            </span>
          )}
          <span style={{ fontSize: '0.85em', color: 'var(--text-dim)' }}>
            Model: {job.model}
          </span>
        </div>
      </div>

      {/* Progress */}
      {(job.status === 'extracting' || job.status === 'analyzing') && (
        <div className="doc-detail-progress">
          {job.total_sections > 0 ? (
            <>
              <ProgressBar current={job.current_section} total={job.total_sections} />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontSize: '0.85em' }}>
                <span>{job.step}</span>
                <span>{job.current_section}/{job.total_sections} sections</span>
              </div>
            </>
          ) : (
            <>
              <div style={{
                height: 6, background: 'var(--border)', borderRadius: 3,
                overflow: 'hidden', position: 'relative',
              }}>
                <div style={{
                  position: 'absolute', inset: 0,
                  background: 'linear-gradient(90deg, transparent, #8b5cf6, transparent)',
                  animation: 'doc-indet 1.5s linear infinite',
                }} />
              </div>
              <div style={{ marginTop: 4, fontSize: '0.85em' }}>
                <span>{job.step}</span>
              </div>
            </>
          )}
          <button className="doc-cancel-btn" onClick={handleCancel} style={{ marginTop: 8 }}>
            Cancel
          </button>
        </div>
      )}

      {/* Stats */}
      <div className="doc-stats-row">
        <div className="doc-stat">
          <div className="doc-stat-value">{job.proposal_ids.length}</div>
          <div className="doc-stat-label">Proposals</div>
        </div>
        <div className="doc-stat">
          <div className="doc-stat-value">{job.total_sections}</div>
          <div className="doc-stat-label">Sections</div>
        </div>
        <div className="doc-stat">
          <div className="doc-stat-value">${job.cost_usd.toFixed(4)}</div>
          <div className="doc-stat-label">Cost</div>
        </div>
        <div className="doc-stat">
          <div className="doc-stat-value">{(job.input_tokens + job.output_tokens).toLocaleString()}</div>
          <div className="doc-stat-label">Tokens</div>
        </div>
        <div className="doc-stat">
          <div className="doc-stat-value">{job.duplicates_flagged}</div>
          <div className="doc-stat-label">Duplicates</div>
        </div>
      </div>

      {/* Metadata */}
      <div className="doc-meta-grid">
        {job.source_type && <div><strong>Source Type:</strong> {job.source_type}</div>}
        {job.authority_level && <div><strong>Authority:</strong> {job.authority_level}</div>}
        {job.citation && <div><strong>Citation:</strong> {job.citation}</div>}
        {job.department && <div><strong>Department:</strong> {job.department}</div>}
        {job.role_key && <div><strong>Role:</strong> {job.role_key}</div>}
      </div>

      {/* Structure Tree */}
      {structure && structure.sections.length > 0 && (
        <div className="doc-structure">
          <h4 style={{ margin: '0 0 8px', fontSize: '0.95em' }}>Document Structure</h4>
          <div className="doc-structure-tree">
            {structure.sections.map((sec, i) => {
              const isProcessed = job.current_section > i;
              const isCurrent = job.current_section === i + 1 && job.status === 'extracting';
              return (
                <div
                  key={sec.id}
                  className="doc-structure-node"
                  style={{ paddingLeft: (sec.level - 1) * 16 }}
                >
                  <span className={`doc-structure-dot ${isProcessed ? 'done' : isCurrent ? 'active' : ''}`} />
                  <span style={{ fontSize: '0.9em' }}>{sec.title}</span>
                  {sec.page_start != null && (
                    <span style={{ fontSize: '0.8em', color: 'var(--text-dim)', marginLeft: 8 }}>
                      p.{sec.page_start}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Errors */}
      {job.errors.length > 0 && (
        <div className="doc-errors">
          <h4 style={{ margin: '0 0 8px', color: '#ef4444', fontSize: '0.95em' }}>Errors</h4>
          {job.errors.map((err, i) => (
            <div key={i} style={{ fontSize: '0.85em', marginBottom: 4, color: '#ef4444' }}>
              {err}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──

export default function DocumentIngestPage() {
  const [jobs, setJobs] = useState<DocumentIngestJob[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const data = await fetchDocumentJobs();
      setJobs(data);
    } catch {
      // ignore load errors
    }
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // Poll active jobs
  useEffect(() => {
    const hasActive = jobs.some(j => j.status === 'extracting' || j.status === 'analyzing');
    if (!hasActive) return;
    const interval = setInterval(loadJobs, 5000);
    return () => clearInterval(interval);
  }, [jobs, loadJobs]);

  const handleUploaded = (job: DocumentIngestJob) => {
    setJobs(prev => [job, ...prev]);
    setSelectedJobId(job.id);
    setViewMode('detail');
  };

  const handleSelectJob = (job: DocumentIngestJob) => {
    setSelectedJobId(job.id);
    setViewMode('detail');
  };

  return (
    <div className="doc-ingest-page">
      <h2 style={{ margin: '0 0 16px', fontSize: '1.2em' }}>Document Ingest</h2>

      {viewMode === 'list' && (
        <>
          <UploadForm onUploaded={handleUploaded} />
          <JobList jobs={jobs} onSelect={handleSelectJob} onRefresh={loadJobs} />
        </>
      )}

      {viewMode === 'detail' && selectedJobId && (
        <JobDetail
          jobId={selectedJobId}
          onBack={() => { setViewMode('list'); loadJobs(); }}
        />
      )}
    </div>
  );
}
