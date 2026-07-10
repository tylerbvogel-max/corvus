import { useEffect, useState } from 'react';
import { MindStyle } from './mindUi';

/** Episode browser — the memory's inputs: every captured or backfilled
 *  session, its signal, and what distillation made of it. */

export default function MindSessionsPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/metrics/mind/sessions').then(r => r.json())
      .then(d => setRows(d.sessions ?? [])).catch(e => setError(String(e)));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;

  return (
    <div className="mm-root">
      <MindStyle />
      <h2>Sessions — the memory's diet</h2>
      <div className="mm-sub">Every captured or backfilled episode log; distillation turns them into lessons.</div>
      <div className="mm-card" style={{ marginTop: '1rem' }}>
        <table className="mm-table"><thead>
          <tr><th>session</th><th>project</th><th style={{ textAlign: 'right' }}>events</th>
            <th style={{ textAlign: 'right' }}>errors</th><th style={{ textAlign: 'right' }}>injections</th>
            <th>distilled</th><th>attribution</th><th>captured</th></tr>
        </thead><tbody>
          {rows.map(r => (
            <tr key={r.session}>
              <td className="mm-key" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.72rem' }}>{r.session.slice(0, 28)}</td>
              <td><span className="mm-chip">{r.project ?? '—'}</span></td>
              <td className="num">{r.events}</td>
              <td className="num" style={{ color: r.errors > 0 ? 'var(--mm-serious)' : undefined }}>{r.errors}</td>
              <td className="num">{r.injections}</td>
              <td>{r.distilled
                ? <span className="mm-badge" style={{ color: 'var(--mm-good)' }}
                    title={`${r.distilled.candidates} candidates, $${r.distilled.cost_usd}`}>
                    ✓ {r.distilled.saved} lessons</span>
                : <span className="mm-badge">queued</span>}</td>
              <td className="mm-sub">{r.distilled?.attribution
                ? `+${r.distilled.attribution.rewarded ?? 0} / −${r.distilled.attribution.penalized ?? 0} / ~${r.distilled.attribution.unused ?? 0}`
                : ''}</td>
              <td className="mm-sub">{r.mtime?.slice(0, 16).replace('T', ' ')}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </div>
  );
}
