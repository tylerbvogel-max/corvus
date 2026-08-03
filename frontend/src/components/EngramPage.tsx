import { useEffect, useState } from 'react';
import { createEngram, fetchEngram, fetchEngramGaps, fetchEngramSummary, fetchEngrams, resolveEngram } from '../api/knowledge_graph';

interface EngramSummary {
  id: number;
  label: string;
  summary: string | null;
  cfr_title: number;
  cfr_part: string;
  cfr_section: string | null;
  source_api: string;
  authority_level: string;
  issuing_body: string | null;
  invocations: number;
  avg_utility: number;
  cached: boolean;
  cached_at: string | null;
  cached_token_count: number | null;
  has_embedding: boolean;
  linked_neurons: number;
  is_active: boolean;
}

interface EngramStats { total: number; active: number; cached: number; embedded: number; }

interface CoverageGap {
  id: number;
  citation_pattern: string;
  family: string | null;
  detection_count: number;
  query_count: number;
  status: string;
  last_detected_at: string | null;
}

interface ResolveResult {
  engram_id: number;
  cfr_ref: string;
  token_count: number;
  text_preview: string;
  cached_at: string;
}

interface SeedDraft {
  cfr_title: string;
  cfr_part: string;
  cfr_section: string;
  label: string;
  summary: string;
  content: string;
  authority_level: string;
  issuing_body: string;
  gap_id: number | null;
}

const EMPTY_DRAFT: SeedDraft = {
  cfr_title: '48', cfr_part: '', cfr_section: '', label: '', summary: '',
  content: '', authority_level: 'regulatory', issuing_body: '', gap_id: null,
};

function cfrRef(e: EngramSummary): string {
  const section = e.cfr_section ? `.${e.cfr_section}` : '';
  return `${e.cfr_title} CFR ${e.cfr_part}${section}`;
}

// "48 CFR 31.205-6" -> { cfr_title:'48', cfr_part:'31', cfr_section:'205-6' }
function parseCfr(pattern: string): { cfr_title: string; cfr_part: string; cfr_section: string } {
  const m = pattern.match(/^(\d+)\s+CFR\s+([\dA-Za-z]+)(?:\.(.+))?$/i);
  if (!m) return { cfr_title: '48', cfr_part: '', cfr_section: '' };
  return { cfr_title: m[1], cfr_part: m[2], cfr_section: m[3] || '' };
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: ok ? 'var(--green, #22c55e)' : 'var(--text-dim, #666)' }} />;
}

const GAP_CAP = 100; // render at most this many gap rows

export default function EngramPage() {
  const [engrams, setEngrams] = useState<EngramSummary[]>([]);
  const [stats, setStats] = useState<EngramStats | null>(null);
  const [gaps, setGaps] = useState<CoverageGap[]>([]);
  const [loading, setLoading] = useState(true);
  const [resolving, setResolving] = useState<number | null>(null);
  const [resolveResult, setResolveResult] = useState<ResolveResult | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [detailCache, setDetailCache] = useState<Record<number, { content: string | null }>>({});

  const [draft, setDraft] = useState<SeedDraft | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    const [e, s, g] = await Promise.all([
      fetchEngrams<any>().catch(() => []),
      fetchEngramSummary<any>().catch(() => null),
      fetchEngramGaps<any>().catch(() => []),
    ]);
    setEngrams(Array.isArray(e) ? e : []);
    setStats(s && !s.error ? s : null);
    setGaps(Array.isArray(g) ? g : []);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleResolve(id: number) {
    setResolving(id);
    try {
      const resp = await resolveEngram<any>(id);
      if (!resp.error) {
        setResolveResult(resp);
        setEngrams(await fetchEngrams<any>());
      }
    } finally { setResolving(null); }
  }

  function openBlankSeed() { setSeedError(null); setDraft({ ...EMPTY_DRAFT }); }
  function seedFromGap(g: CoverageGap) {
    setSeedError(null);
    const c = parseCfr(g.citation_pattern);
    setDraft({ ...EMPTY_DRAFT, ...c, label: `${g.citation_pattern}: `, gap_id: g.id });
  }

  async function submitSeed() {
    if (!draft) return;
    setSeeding(true); setSeedError(null);
    try {
      const body = {
        cfr_title: parseInt(draft.cfr_title, 10),
        cfr_part: draft.cfr_part,
        cfr_section: draft.cfr_section || null,
        label: draft.label,
        summary: draft.summary || null,
        content: draft.content || null,
        authority_level: draft.authority_level,
        issuing_body: draft.issuing_body || null,
        gap_id: draft.gap_id,
      };
      const resp = await createEngram<any>(body);
      if (resp.error) { setSeedError(resp.error); return; }
      setDraft(null);
      await load();
    } catch (err) {
      setSeedError(err instanceof Error ? err.message : 'Failed to create engram');
    } finally { setSeeding(false); }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Loading engrams…</div>;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1320 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h2 style={{ color: 'var(--text)', fontSize: 20, fontWeight: 700, margin: '0 0 6px' }}>Engrams</h2>
          <p style={{ color: 'var(--text-dim)', fontSize: 13, margin: 0, maxWidth: 720 }}>
            Retrieval indices for external regulatory sources. Engrams score alongside neurons,
            associate with them as they co-fire, and fetch authoritative text live from eCFR at query time.
          </p>
        </div>
        <button onClick={openBlankSeed} style={primaryBtn}>＋ Seed engram</button>
      </div>

      {/* Stats bar */}
      {stats && (
        <div style={statsBar}>
          {[
            { label: 'Total', value: stats.total, color: 'var(--text)' },
            { label: 'Active', value: stats.active, color: 'var(--green, #22c55e)' },
            { label: 'Embedded', value: stats.embedded, color: 'var(--blue, #4a9eff)' },
            { label: 'Cached', value: stats.cached, color: 'var(--amber, #f59e0b)' },
            { label: 'Coverage gaps', value: gaps.length, color: gaps.length ? 'var(--accent, #c87533)' : 'var(--text-dim)' },
          ].map(s => (
            <div key={s.label} style={{ textAlign: 'center', minWidth: 78 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: 'monospace' }}>{s.value}</div>
              <div style={{ fontSize: 10, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: 1 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Seed form */}
      {draft && (
        <SeedForm
          draft={draft} setDraft={setDraft} onSubmit={submitSeed}
          onCancel={() => setDraft(null)} seeding={seeding} error={seedError}
        />
      )}

      {/* Coverage gaps panel */}
      <CoverageGaps gaps={gaps} onSeed={seedFromGap} />

      {/* Engram table */}
      <div style={{ borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)', background: 'var(--bg-card)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: 'var(--bg-input)' }}>
              <th style={thStyle}>ID</th>
              <th style={{ ...thStyle, textAlign: 'left' }}>CFR Reference</th>
              <th style={{ ...thStyle, textAlign: 'left' }}>Label</th>
              <th style={{ ...thStyle, textAlign: 'left' }}>Authority</th>
              <th style={{ ...thStyle, textAlign: 'left' }}>Issuing body</th>
              <th style={thStyle} title="Neurons associated via co-firing">Linked</th>
              <th style={thStyle}>Embed</th>
              <th style={thStyle}>Cache</th>
              <th style={thStyle}>Tokens</th>
              <th style={thStyle}>Fires</th>
              <th style={thStyle}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {engrams.map(e => (
              <EngramRow
                key={e.id}
                engram={e}
                expanded={expanded === e.id}
                onToggle={() => {
                  const next = expanded === e.id ? null : e.id;
                  setExpanded(next);
                  if (next !== null && !detailCache[e.id]) {
                    fetchEngram<any>(e.id).then(d => {
                      setDetailCache(prev => ({ ...prev, [e.id]: { content: d.content } }));
                    });
                  }
                }}
                onResolve={() => handleResolve(e.id)}
                resolving={resolving === e.id}
                content={detailCache[e.id]?.content ?? null}
              />
            ))}
            {engrams.length === 0 && (
              <tr><td colSpan={11} style={{ padding: 24, textAlign: 'center', color: 'var(--text-dim)' }}>
                No engrams yet. Use “＋ Seed engram” or check tenant engram_seeds.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Resolve result panel */}
      {resolveResult && (
        <div style={{ marginTop: 16, padding: '16px 20px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--green, #22c55e)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ color: 'var(--green, #22c55e)', fontWeight: 700, fontSize: 13 }}>Resolved: {resolveResult.cfr_ref}</span>
            <span style={{ color: 'var(--text-dim)', fontSize: 11, fontFamily: 'monospace' }}>
              {resolveResult.token_count.toLocaleString()} tokens · cached {resolveResult.cached_at}
            </span>
          </div>
          <pre style={{ color: 'var(--text-dim)', fontSize: 11, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 300, overflow: 'auto', background: 'var(--bg-input)', padding: 12, borderRadius: 6, margin: 0 }}>
            {resolveResult.text_preview}
          </pre>
          <button onClick={() => setResolveResult(null)} style={{ marginTop: 8, padding: '4px 12px', fontSize: 11, background: 'none', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-dim)', cursor: 'pointer' }}>Dismiss</button>
        </div>
      )}
    </div>
  );
}

function CoverageGaps({ gaps, onSeed }: { gaps: CoverageGap[]; onSeed: (g: CoverageGap) => void }) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{ marginBottom: 20, borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-card)', overflow: 'hidden' }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 16px', cursor: 'pointer', background: 'var(--bg-input)' }}>
        <span style={{ color: 'var(--accent, #c87533)', fontSize: 13, fontWeight: 700 }}>Coverage gaps</span>
        <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          {gaps.length} regulation{gaps.length === 1 ? '' : 's'} cited in answers but not covered by an engram
        </span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-dim)', fontSize: 12 }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        gaps.length === 0 ? (
          <div style={{ padding: 16, color: 'var(--text-dim)', fontSize: 12 }}>
            None yet — as answers cite CFR references we don’t have an engram for, they’ll queue here as candidates to seed.
          </div>
        ) : (
          <div style={{ maxHeight: 320, overflow: 'auto' }}>
            {gaps.slice(0, GAP_CAP).map(g => (
              <div key={g.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '7px 16px', borderTop: '1px solid var(--border)', fontSize: 12 }}>
                <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--accent, #c87533)', minWidth: 150 }}>{g.citation_pattern}</span>
                <span style={{ color: 'var(--text-dim)', minWidth: 90 }}>{g.family || '—'}</span>
                <span style={{ color: 'var(--text-dim)', fontFamily: 'monospace' }} title="times cited across answers">×{g.detection_count}</span>
                {g.query_count > 0 && <span style={{ color: 'var(--text-dim)', fontFamily: 'monospace' }}>{g.query_count} quer{g.query_count === 1 ? 'y' : 'ies'}</span>}
                <button onClick={() => onSeed(g)} style={{ ...ghostBtn, marginLeft: 'auto' }}>Seed →</button>
              </div>
            ))}
            {gaps.length > GAP_CAP && (
              <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', color: 'var(--text-dim)', fontSize: 11 }}>
                Showing top {GAP_CAP} of {gaps.length} by citation frequency.
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

function SeedForm({ draft, setDraft, onSubmit, onCancel, seeding, error }: {
  draft: SeedDraft; setDraft: (d: SeedDraft) => void;
  onSubmit: () => void; onCancel: () => void; seeding: boolean; error: string | null;
}) {
  const set = (k: keyof SeedDraft, v: string) => setDraft({ ...draft, [k]: v });
  const canSubmit = draft.cfr_title.trim() && draft.cfr_part.trim() && draft.label.trim() && !seeding;
  return (
    <div style={{ marginBottom: 20, padding: '16px 20px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--accent, #c87533)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ color: 'var(--accent, #c87533)', fontWeight: 700, fontSize: 13 }}>
          {draft.gap_id != null ? 'Seed engram from coverage gap' : 'Seed a new engram'}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '90px 120px 140px 1fr', gap: 10, marginBottom: 10 }}>
        <Field label="CFR Title"><input style={inp} value={draft.cfr_title} onChange={e => set('cfr_title', e.target.value)} placeholder="48" /></Field>
        <Field label="Part"><input style={inp} value={draft.cfr_part} onChange={e => set('cfr_part', e.target.value)} placeholder="252" /></Field>
        <Field label="Section"><input style={inp} value={draft.cfr_section} onChange={e => set('cfr_section', e.target.value)} placeholder="204-7012" /></Field>
        <Field label="Authority">
          <select style={inp} value={draft.authority_level} onChange={e => set('authority_level', e.target.value)}>
            <option value="regulatory">regulatory</option>
            <option value="statute">statute</option>
            <option value="standard">standard</option>
            <option value="guidance">guidance</option>
          </select>
        </Field>
      </div>
      <div style={{ marginBottom: 10 }}>
        <Field label="Label (required)"><input style={inp} value={draft.label} onChange={e => set('label', e.target.value)} placeholder="48 CFR 252.204-7012: Safeguarding Covered Defense Information" /></Field>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
        <Field label="Summary"><textarea style={{ ...inp, height: 52, resize: 'vertical' }} value={draft.summary} onChange={e => set('summary', e.target.value)} placeholder="One-line description of what the regulation covers." /></Field>
        <Field label="Retrieval cues (content)"><textarea style={{ ...inp, height: 52, resize: 'vertical' }} value={draft.content} onChange={e => set('content', e.target.value)} placeholder="key terms that should trigger this engram (embedded, NOT the reg text)" /></Field>
      </div>
      <div style={{ marginBottom: 12 }}>
        <Field label="Issuing body"><input style={inp} value={draft.issuing_body} onChange={e => set('issuing_body', e.target.value)} placeholder="Department of Defense (DFARS)" /></Field>
      </div>
      {error && <div style={{ color: 'var(--red, #ef4444)', fontSize: 12, marginBottom: 10 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={onSubmit} disabled={!canSubmit} style={{ ...primaryBtn, opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? 'pointer' : 'not-allowed' }}>
          {seeding ? 'Creating…' : 'Create engram'}
        </button>
        <button onClick={onCancel} style={ghostBtn}>Cancel</button>
        <span style={{ alignSelf: 'center', color: 'var(--text-dim)', fontSize: 11 }}>Text is fetched from eCFR on first Resolve; embedding is generated now.</span>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block' }}>
      <span style={{ display: 'block', color: 'var(--text-dim)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>{label}</span>
      {children}
    </label>
  );
}

function EngramRow({ engram: e, expanded, onToggle, onResolve, resolving, content }: {
  engram: EngramSummary; expanded: boolean; onToggle: () => void; onResolve: () => void; resolving: boolean; content: string | null;
}) {
  const ref = cfrRef(e);
  return (
    <>
      <tr style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }} onClick={onToggle}>
        <td style={{ ...td, textAlign: 'center', color: 'var(--text-dim)' }}>{e.id}</td>
        <td style={td}>
          <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontFamily: 'monospace', fontWeight: 600, background: 'var(--accent, #c87533)20', color: 'var(--accent, #c87533)' }}>{ref}</span>
        </td>
        <td style={{ ...td, color: 'var(--text)', fontSize: 12, maxWidth: 240 }}>{e.label.replace(/^\d+ CFR [\dA-Za-z.-]+: /, '')}</td>
        <td style={td}><span style={authChip(e.authority_level)}>{e.authority_level}</span></td>
        <td style={{ ...td, color: 'var(--text-dim)', fontSize: 11, maxWidth: 170, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={e.issuing_body || ''}>{e.issuing_body || '—'}</td>
        <td style={{ ...td, textAlign: 'center', fontFamily: 'monospace', color: e.linked_neurons ? 'var(--blue, #4a9eff)' : 'var(--text-dim)' }}>{e.linked_neurons}</td>
        <td style={{ ...td, textAlign: 'center' }}><StatusDot ok={e.has_embedding} /></td>
        <td style={{ ...td, textAlign: 'center' }}><StatusDot ok={e.cached} /></td>
        <td style={{ ...td, textAlign: 'center', color: 'var(--text-dim)', fontFamily: 'monospace', fontSize: 11 }}>{e.cached_token_count != null ? e.cached_token_count.toLocaleString() : '—'}</td>
        <td style={{ ...td, textAlign: 'center', color: 'var(--text-dim)', fontFamily: 'monospace', fontSize: 11 }}>{e.invocations}</td>
        <td style={{ ...td, textAlign: 'center' }}>
          <button onClick={ev => { ev.stopPropagation(); onResolve(); }} disabled={resolving} style={{ padding: '3px 10px', fontSize: 10, borderRadius: 4, background: resolving ? 'var(--bg-input)' : 'var(--accent, #c87533)20', border: '1px solid var(--accent, #c87533)40', color: 'var(--accent, #c87533)', cursor: resolving ? 'wait' : 'pointer', fontWeight: 600 }}>{resolving ? 'Fetching…' : 'Resolve'}</button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={11} style={{ padding: '8px 20px 16px', background: 'var(--bg-input)' }}>
            <div style={{ fontSize: 11, marginBottom: 10 }}>
              <span style={{ color: 'var(--text-dim)' }}>Summary: </span>
              <span style={{ color: 'var(--text)' }}>{e.summary || '—'}</span>
            </div>
            <div style={{ marginBottom: 10, padding: '8px 12px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--accent, #c87533)30' }}>
              <div style={{ color: 'var(--accent, #c87533)', fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Retrieval cues (content)</div>
              <div style={{ color: 'var(--text)', fontSize: 11, fontFamily: 'monospace', lineHeight: 1.6 }}>{content ?? 'loading…'}</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '6px 24px', fontSize: 11 }}>
              <div><span style={{ color: 'var(--text-dim)' }}>Source API: </span><span style={{ color: 'var(--text)', fontFamily: 'monospace' }}>{e.source_api}</span></div>
              <div><span style={{ color: 'var(--text-dim)' }}>Utility: </span><span style={{ color: 'var(--text)', fontFamily: 'monospace' }}>{e.avg_utility.toFixed(2)}</span></div>
              <div><span style={{ color: 'var(--text-dim)' }}>Cached at: </span><span style={{ color: 'var(--text)', fontFamily: 'monospace' }}>{e.cached_at || 'not cached'}</span></div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function authChip(level: string): React.CSSProperties {
  const color = level === 'statute' ? 'var(--red, #ef4444)'
    : level === 'standard' ? 'var(--blue, #4a9eff)'
      : level === 'guidance' ? 'var(--text-dim, #888)' : 'var(--green, #22c55e)';
  return { display: 'inline-block', padding: '1px 7px', borderRadius: 3, fontSize: 10, fontWeight: 600, color, background: `${color}1e` };
}

const thStyle: React.CSSProperties = { padding: '10px 12px', color: 'var(--text-dim)', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, textAlign: 'center' };
const td: React.CSSProperties = { padding: '8px 12px' };
const statsBar: React.CSSProperties = { display: 'flex', gap: 20, marginBottom: 20, padding: '12px 16px', borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--border)' };
const inp: React.CSSProperties = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 5, padding: '5px 8px', fontSize: 12, fontFamily: 'inherit' };
const primaryBtn: React.CSSProperties = { padding: '7px 14px', fontSize: 12, fontWeight: 600, borderRadius: 6, background: 'var(--accent, #c87533)', border: '1px solid var(--accent, #c87533)', color: '#1a1204', cursor: 'pointer' };
const ghostBtn: React.CSSProperties = { padding: '4px 12px', fontSize: 11, fontWeight: 600, borderRadius: 5, background: 'none', border: '1px solid var(--border)', color: 'var(--text-dim)', cursor: 'pointer' };
