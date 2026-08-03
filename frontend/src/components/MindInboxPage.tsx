import { useEffect, useState } from 'react';
import { MindStyle } from './mindUi';
import { fetchInbox } from '../api/memory';

/** Needs-your-judgment inbox: the policy plane. Open contradiction
 *  findings, write-gate-queued proposals, and the consolidation
 *  janitor's borderline pairs — everything only a human can settle. */

export default function MindInboxPage() {
  const [inbox, setInbox] = useState<any>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchInbox<any>().then(setInbox).catch(e => setError(String(e)));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;
  if (!inbox) return <div className="detail-empty">Loading inbox…</div>;

  const total = inbox.findings.length + inbox.proposals.length + inbox.borderline_pairs.length;

  return (
    <div className="mm-root">
      <MindStyle />
      <h2>Inbox — needs your judgment</h2>
      <div className="mm-sub">{total === 0 ? 'Nothing awaits you. The system is self-resolving.' : `${total} items the system cannot settle alone.`}</div>
      <div className="mm-grid">
        <div className="mm-card">
          <h3>Open integrity findings ({inbox.findings.length})</h3>
          {inbox.findings.length === 0 && <div className="mm-empty">none</div>}
          <table className="mm-table"><tbody>
            {inbox.findings.map((f: any) => (
              <tr key={f.id}>
                <td className="num">#{f.id}</td>
                <td><span className="mm-chip">{f.type}</span></td>
                <td className="mm-key">{f.description} <span className="mm-sub">neurons {JSON.stringify(f.neuron_ids)}</span></td>
              </tr>
            ))}
          </tbody></table>
          <div className="mm-sub" style={{ marginTop: 6 }}>Resolve in Integrity Findings.</div>
        </div>
        <div className="mm-card">
          <h3>Queued proposals ({inbox.proposals.length})</h3>
          {inbox.proposals.length === 0 && <div className="mm-empty">none — the write gate is auto-committing within policy</div>}
          <table className="mm-table"><tbody>
            {inbox.proposals.map((p: any) => (
              <tr key={p.id}><td className="num">#{p.id}</td>
                <td><span className="mm-chip">{p.source}</span></td>
                <td className="mm-key">{p.description}</td></tr>
            ))}
          </tbody></table>
          <div className="mm-sub" style={{ marginTop: 6 }}>Approve or reject in Proposal Queue.</div>
        </div>
        <div className="mm-card">
          <h3>Borderline consolidation pairs ({inbox.borderline_pairs.length})</h3>
          {inbox.borderline_pairs.length === 0 && <div className="mm-empty">none</div>}
          <table className="mm-table"><tbody>
            {inbox.borderline_pairs.map((b: any, i: number) => (
              <tr key={i}>
                <td className="mm-key">#{b.a} ↔ #{b.b}<br /><span className="mm-sub">{(b.labels ?? []).join('  ·  ')}</span></td>
                <td className="num">{b.sim}</td>
                <td><span className="mm-badge">{b.verdict ?? 'unjudged'}</span></td>
              </tr>
            ))}
          </tbody></table>
          <div className="mm-sub" style={{ marginTop: 6 }}>The janitor kept both; fuse manually in Explorer if one is redundant.</div>
        </div>
      </div>
    </div>
  );
}
