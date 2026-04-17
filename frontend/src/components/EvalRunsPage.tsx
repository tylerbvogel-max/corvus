import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  certifyEvalRun,
  getEvalRun,
  listEvalRuns,
  startEvalRun,
  type EvalRunDetail,
  type EvalRunSummary,
} from '../api';

type AsyncStatus = 'idle' | 'loading' | 'ready' | 'error';

const STATUS_COLOR: Record<string, string> = {
  running: '#e8a838',
  completed: '#4caf50',
  failed: '#e74c3c',
};

export default function EvalRunsPage() {
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [status, setStatus] = useState<AsyncStatus>('idle');
  const [detailStatus, setDetailStatus] = useState<AsyncStatus>('idle');
  const [suiteName, setSuiteName] = useState('smoke');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setStatus('loading');
    try {
      const rows = await listEvalRuns(50);
      setRuns(rows);
      setStatus('ready');
    } catch (exc) {
      setError((exc as Error).message);
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    setDetailStatus('loading');
    getEvalRun(selectedId)
      .then(d => { setDetail(d); setDetailStatus('ready'); })
      .catch(exc => { setError((exc as Error).message); setDetailStatus('error'); });
  }, [selectedId]);

  const handleStart = async () => {
    if (!suiteName.trim()) return;
    setStarting(true);
    setError(null);
    try {
      const created = await startEvalRun(suiteName.trim());
      await refresh();
      setSelectedId(created.id);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const handleCertify = async (id: number) => {
    setError(null);
    try {
      await certifyEvalRun(id);
      await refresh();
      if (selectedId === id) setSelectedId(id); // re-trigger detail refresh
    } catch (exc) {
      setError((exc as Error).message);
    }
  };

  const certifiedId = useMemo(
    () => runs.find(r => r.is_certified)?.id ?? null,
    [runs],
  );

  return (
    <div className="info-page" style={{ padding: '1rem' }}>
      <h2 className="info-title">Eval Runs</h2>
      <p className="info-subtitle">
        Immutable eval artifacts (AIP Pattern #3). Certified run ID propagates
        to every <code>/v1/query</code> response as <code>eval_run_id</code>.
      </p>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '1rem 0' }}>
        <input
          value={suiteName}
          onChange={e => setSuiteName(e.target.value)}
          placeholder="suite name (e.g. smoke)"
          style={{
            background: 'var(--bg-input)', color: 'var(--text)',
            border: '1px solid var(--border)', borderRadius: 4,
            padding: '6px 10px', fontSize: '0.85rem', minWidth: 220,
          }}
        />
        <button onClick={handleStart} disabled={starting} style={buttonStyle}>
          {starting ? 'Running…' : 'Start run'}
        </button>
        <button onClick={refresh} style={buttonStyle}>Refresh</button>
        <div style={{ marginLeft: 'auto', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {certifiedId !== null
            ? <>Certified: <code>#{certifiedId}</code></>
            : <em>No run certified yet — /v1/query returns null eval_run_id</em>}
        </div>
      </div>

      {error && (
        <div style={{
          color: '#e74c3c', background: 'rgba(231,76,60,0.08)',
          border: '1px solid #e74c3c', padding: 8, borderRadius: 4,
          fontSize: '0.85rem', marginBottom: 12,
        }}>{error}</div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: 16 }}>
        <div>
          <h3 style={{ margin: '0 0 8px', fontSize: '0.95rem' }}>Recent runs</h3>
          {status === 'loading' && <div>Loading…</div>}
          {status === 'ready' && runs.length === 0 && <div><em>No runs yet.</em></div>}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '6px 4px' }}>ID</th>
                <th>Suite</th>
                <th>Status</th>
                <th>Pass</th>
                <th>Blocked</th>
                <th>Certified</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr
                  key={r.id}
                  onClick={() => setSelectedId(r.id)}
                  style={{
                    cursor: 'pointer',
                    background: r.id === selectedId ? 'rgba(33,150,243,0.1)' : 'transparent',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <td style={{ padding: '6px 4px' }}>#{r.id}</td>
                  <td>{r.suite_name}</td>
                  <td>
                    <span style={{
                      color: STATUS_COLOR[r.status] ?? 'var(--text)',
                      fontWeight: 600,
                    }}>{r.status}</span>
                  </td>
                  <td>{r.summary ? `${(r.summary.pass_rate * 100).toFixed(0)}%` : '—'}</td>
                  <td>{r.summary ? r.summary.blocked : '—'}</td>
                  <td>{r.is_certified ? '✓' : ''}</td>
                  <td>
                    {r.status === 'completed' && !r.is_certified && (
                      <button
                        onClick={e => { e.stopPropagation(); handleCertify(r.id); }}
                        style={{ ...buttonStyle, padding: '2px 8px', fontSize: '0.75rem' }}
                      >Certify</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <h3 style={{ margin: '0 0 8px', fontSize: '0.95rem' }}>Detail</h3>
          {selectedId === null && <div><em>Select a run to inspect its cases.</em></div>}
          {detailStatus === 'loading' && <div>Loading…</div>}
          {detail && <EvalRunDetailView detail={detail} />}
        </div>
      </div>
    </div>
  );
}

function EvalRunDetailView({ detail }: { detail: EvalRunDetail }) {
  return (
    <div style={{ fontSize: '0.85rem' }}>
      <div style={{ marginBottom: 10 }}>
        <div><strong>#{detail.id}</strong> · {detail.suite_name}
          {detail.is_certified && (
            <span style={{ marginLeft: 8, color: '#4caf50', fontWeight: 600 }}>
              CERTIFIED
            </span>
          )}
        </div>
        <div style={{ color: 'var(--text-muted)' }}>
          started {new Date(detail.started_at).toLocaleString()} by {detail.started_by}
        </div>
        <div style={{ color: 'var(--text-muted)' }}>
          suite_hash {detail.suite_hash.slice(0, 16)}… ·
          engine {detail.scoring_engine_version}
        </div>
      </div>

      {detail.summary && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 8, margin: '8px 0', fontSize: '0.8rem',
        }}>
          <Stat label="Total" value={detail.summary.total} />
          <Stat label="Pass rate" value={`${(detail.summary.pass_rate * 100).toFixed(0)}%`} />
          <Stat label="Blocked" value={detail.summary.blocked} />
          <Stat label="Errors" value={detail.summary.errors} />
          <Stat label="Violations" value={detail.summary.violation_count} />
        </div>
      )}

      <h4 style={{ margin: '12px 0 4px' }}>Cases</h4>
      {detail.cases.map(c => (
        <div key={c.id} style={{
          border: '1px solid var(--border)', borderRadius: 4,
          padding: 8, marginBottom: 6,
        }}>
          <div><strong>{c.case_label}</strong>
            {c.blocked && <span style={{ color: '#e74c3c', marginLeft: 8 }}>BLOCKED</span>}
            {c.error_message && <span style={{ color: '#e8a838', marginLeft: 8 }}>ERROR</span>}
            {c.lineage_id && (
              <span style={{ marginLeft: 8, color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                lineage #{c.lineage_id}
              </span>
            )}
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '2px 0' }}>
            {c.query_text}
          </div>
          {c.response_text && (
            <details style={{ marginTop: 4 }}>
              <summary style={{ cursor: 'pointer', fontSize: '0.75rem' }}>Response</summary>
              <pre style={{
                whiteSpace: 'pre-wrap', fontSize: '0.75rem',
                margin: '4px 0 0', padding: 6, background: 'var(--bg-input)',
                borderRadius: 4,
              }}>{c.response_text}</pre>
            </details>
          )}
          {c.violations.length > 0 && (
            <div style={{ fontSize: '0.75rem', color: '#e8a838', marginTop: 4 }}>
              {c.violations.length} violation{c.violations.length > 1 ? 's' : ''}
            </div>
          )}
          {c.error_message && (
            <div style={{ fontSize: '0.75rem', color: '#e74c3c', marginTop: 4 }}>
              {c.error_message}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div style={{
      padding: 6, border: '1px solid var(--border)',
      borderRadius: 4, textAlign: 'center',
    }}>
      <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>{label}</div>
      <div style={{ fontSize: '1rem', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const buttonStyle: React.CSSProperties = {
  background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 4,
  padding: '6px 12px', fontSize: '0.85rem',
  cursor: 'pointer',
};
