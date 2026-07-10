import { useEffect, useState } from 'react';
import { MindStyle } from './mindUi';

/** Compiled-skills viewer: the graph's build output. Each skill shows
 *  its source lessons' health; a rotten source means the reverse check
 *  will retract and recompile it on the next compiler run. */

export default function MindSkillsPage() {
  const [skills, setSkills] = useState<any[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/metrics/mind/skills').then(r => r.json())
      .then(d => setSkills(d.skills ?? [])).catch(e => setError(String(e)));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;

  return (
    <div className="mm-root">
      <MindStyle />
      <h2>Compiled skills — memory as playbooks</h2>
      <div className="mm-sub">Emitted into ~/.claude/skills/; the graph is the source of truth, these are build output.</div>
      {skills.length === 0 && <div className="mm-empty" style={{ marginTop: '1rem' }}>No skills compiled yet — clusters need ≥3 stable lessons.</div>}
      <div className="mm-grid">
        {skills.map(s => (
          <div className="mm-card" key={s.name}>
            <h3>{s.name} {s.stale && <span className="mm-badge" style={{ color: 'var(--mm-warn)' }}>⚠ stale — will recompile</span>}</h3>
            <div className="mm-sub">{s.scope} · compiled {s.compiled_at}</div>
            <h4>Source lessons</h4>
            <table className="mm-table"><tbody>
              {s.source_health.map((src: any) => (
                <tr key={src.id}>
                  <td className="num">#{src.id}</td>
                  <td className="mm-key">{src.label}</td>
                  <td>{src.healthy
                    ? <span className="mm-badge" style={{ color: 'var(--mm-good)' }}>healthy</span>
                    : <span className="mm-badge" style={{ color: 'var(--mm-serious)' }}>rotten</span>}</td>
                </tr>
              ))}
            </tbody></table>
            <h4><a style={{ cursor: 'pointer' }} onClick={() => setOpen(open === s.name ? null : s.name)}>
              {open === s.name ? '▾ hide SKILL.md' : '▸ view SKILL.md'}</a></h4>
            {open === s.name && <pre className="mm-pre">{s.body ?? '(unreadable)'}</pre>}
          </div>
        ))}
      </div>
    </div>
  );
}
