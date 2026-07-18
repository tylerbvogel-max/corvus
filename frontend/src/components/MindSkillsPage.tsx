import { Fragment, useEffect, useState } from 'react';
import { MindStyle } from './mindUi';

/** Compiled-skills viewer: the graph's build output. One table holds every
 *  skill the episode hook knows about — compiled mind-* skills (expandable
 *  to source-lesson health and the rendered SKILL.md) plus harness/retired
 *  skills whose invocations would otherwise be invisible. */

export default function MindSkillsPage() {
  const [skills, setSkills] = useState<any[]>([]);
  const [others, setOthers] = useState<any[]>([]);
  const [openSources, setOpenSources] = useState<string | null>(null);
  const [openBody, setOpenBody] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/metrics/mind/skills').then(r => r.json())
      .then(d => { setSkills(d.skills ?? []); setOthers(d.uncompiled_invocations ?? []); })
      .catch(e => setError(String(e)));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;

  const kindLabel = (k: string) =>
    k === 'retired-compiled' ? 'retired mind skill'
      : k === 'compiled-unknown' ? 'mind skill (no rendering found)'
      : 'harness skill';

  return (
    <div className="mm-root">
      <MindStyle />
      <h2>Skills — memory as playbooks</h2>
      <div className="mm-sub">
        Compiled skills are build output of the graph (emitted into ~/.claude/skills/); harness and
        retired skills appear so observed invocations are never invisible. Invocation counts are
        Skill-tool loads recorded by the episode hook — only hook-instrumented sessions count.
      </div>
      {skills.length === 0 && others.length === 0 && (
        <div className="mm-empty" style={{ marginTop: '1rem' }}>No skills compiled yet — clusters need ≥3 stable lessons.</div>
      )}
      <table className="mm-table" style={{ marginTop: '0.75rem', width: '100%' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Skill</th>
            <th style={{ textAlign: 'left' }}>Kind</th>
            <th style={{ textAlign: 'left' }}>Scope</th>
            <th className="num">Invoked</th>
            <th style={{ textAlign: 'left' }}>Last</th>
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
                <td><span className="mm-badge">compiled</span></td>
                <td>{s.scope ?? '—'}</td>
                <td className="num" title="Skill-tool loads recorded by the episode hook.">
                  {s.delivery === 'sessionstart-injected' ? '—' : `${s.invocations}×`}
                </td>
                <td>
                  {s.delivery === 'sessionstart-injected'
                    ? <span className="mm-badge" title="The memory hook injects the charter whole at SessionStart — the Skill tool never loads it, so invocation counts don't apply.">injected at SessionStart</span>
                    : (s.last_invoked_at ? String(s.last_invoked_at).slice(0, 10) : (s.invocations > 0 ? '' : 'never'))}
                </td>
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
                  <td colSpan={7} style={{ padding: '0.4rem 1rem 0.8rem' }}>
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
                  <td colSpan={7} style={{ padding: '0.4rem 1rem 0.8rem' }}>
                    <pre className="mm-pre">{s.body ?? '(unreadable)'}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {others.map(o => (
            <tr key={o.name}>
              <td className="mm-key">{o.name}</td>
              <td><span className="mm-badge">{kindLabel(o.kind)}</span></td>
              <td>—</td>
              <td className="num">{o.invocations}×</td>
              <td>{o.last_invoked_at ? String(o.last_invoked_at).slice(0, 10) : ''}</td>
              <td>—</td>
              <td></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
