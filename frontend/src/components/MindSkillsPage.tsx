import { Fragment, useEffect, useState } from 'react';
import { MindStyle } from './mindUi';

/** Compiled-skills viewer: the graph's build output, expandable to source
 *  lesson health and the rendered SKILL.md. */

export default function MindSkillsPage() {
  const [skills, setSkills] = useState<any[]>([]);
  const [openSources, setOpenSources] = useState<string | null>(null);
  const [openBody, setOpenBody] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/metrics/mind/skills').then(r => r.json())
      .then(d => setSkills(d.skills ?? []))
      .catch(e => setError(String(e)));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;

  return (
    <div className="mm-root">
      <MindStyle />
      <h2>Skills — memory as playbooks</h2>
      <div className="mm-sub">
        Compiled skills are durable projections of the graph, rebuilt when their source lessons
        change. Usage is not shown because current telemetry observes only Claude Code Skill-tool
        calls; Codex and native skill loading would be incorrectly reported as “never.”
      </div>
      {skills.length === 0 && (
        <div className="mm-empty" style={{ marginTop: '1rem' }}>No skills compiled yet — clusters need ≥3 stable lessons.</div>
      )}
      <table className="mm-table" style={{ marginTop: '0.75rem', width: '100%' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Skill</th>
            <th style={{ textAlign: 'left' }}>Scope</th>
            <th style={{ textAlign: 'left' }}>Compiled</th>
            <th style={{ textAlign: 'left' }}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {skills.map(s => (
            <Fragment key={s.name}>
              <tr>
                <td className="mm-key">
                  {s.name}
                  {s.stale && <span className="mm-badge" style={{ color: 'var(--mm-warn)', marginLeft: 6 }}>⚠ stale — will recompile</span>}
                </td>
                <td>{s.scope ?? '—'}</td>
                <td>{s.compiled_at ? String(s.compiled_at).slice(0, 10) : '—'}</td>
                <td>
                  <a style={{ cursor: 'pointer', marginRight: 10 }}
                    onClick={() => setOpenSources(openSources === s.name ? null : s.name)}>
                    {openSources === s.name ? '▾' : '▸'} sources ({s.source_health?.length ?? 0})
                  </a>
                  <a style={{ cursor: 'pointer' }}
                    onClick={() => setOpenBody(openBody === s.name ? null : s.name)}>
                    {openBody === s.name ? '▾ hide' : '▸ SKILL.md'}
                  </a>
                </td>
              </tr>
              {openSources === s.name && (
                <tr>
                  <td colSpan={4} style={{ padding: '0.4rem 1rem 0.8rem' }}>
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
                  </td>
                </tr>
              )}
              {openBody === s.name && (
                <tr>
                  <td colSpan={4} style={{ padding: '0.4rem 1rem 0.8rem' }}>
                    <pre className="mm-pre">{s.body ?? '(unreadable)'}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
