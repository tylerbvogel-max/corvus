// Shared telemetry-rendering primitives used by QueryLab.

// ── Formatting helpers ──────────────────────────────────────────────────

export function fmtDuration(ms: number): string {
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)}s`;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms - minutes * 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function fmtCost(v: number): string {
  if (v === 0) return '$0';
  if (v < 0.01) return `$${v.toFixed(4)}`;
  if (v < 1) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(2)}`;
}

export function fmtChipValue(v: unknown): string {
  if (Array.isArray(v)) return `[${v.length}]`;
  if (v === null) return 'null';
  return String(v);
}

// ── Status colors (theme tokens, work across all 5 themes) ──────────────

export function stageStatusColor(status: string): string {
  if (status === 'done') return 'var(--precision)';
  if (status === 'error') return 'var(--impact)';
  return 'var(--text-dim)';
}

// ── Detail-key categorization ───────────────────────────────────────────

// Shortened labels for verbose query-pipeline telemetry keys.
// Keys not listed render with their original name. Maps are conservative —
// only rename when the original name is clunky in a chip.
export const SHORT_KEYS: Record<string, string> = {
  effective_top_k: 'top_k',
  engram_candidates: 'engrams',
};

// Keys whose values identify another DB row — rendered as `{label} #{id}`
// (dim chip), rather than `key={value}`.
const ID_KEYS = new Set(['query_id', 'proposal_id', 'run_id']);
export const ID_LABELS: Record<string, string> = {
  query_id: 'query',
  proposal_id: 'proposal',
  run_id: 'run',
};

export type DetailGroups = {
  cost: number | null;
  counts: [string, unknown][];
  flags: [string, unknown][];
  ids: [string, unknown][];
};

export function categorizeDetail(detail: Record<string, unknown> | undefined): DetailGroups {
  const groups: DetailGroups = { cost: null, counts: [], flags: [], ids: [] };
  if (!detail) return groups;
  for (const [k, v] of Object.entries(detail)) {
    if (k === 'cost_usd' && typeof v === 'number') {
      groups.cost = v;
      continue;
    }
    if (ID_KEYS.has(k)) {
      if (v != null) groups.ids.push([k, v]);
      continue;
    }
    if (typeof v === 'number') {
      groups.counts.push([k, v]);
    } else {
      groups.flags.push([k, v]);
    }
  }
  return groups;
}

// ── Presentational components ───────────────────────────────────────────

export function StatusPill({ status }: { status: string }) {
  const color = stageStatusColor(status);
  return (
    <span
      style={{
        fontSize: '0.65rem', padding: '1px 6px', borderRadius: 3,
        background: `color-mix(in srgb, ${color} 15%, transparent)`,
        color, textTransform: 'uppercase', fontWeight: 600,
        letterSpacing: 0.5,
      }}
    >
      {status}
    </span>
  );
}

// Structured detail chips for a single stage's telemetry. Consumers pass
// either a `groups` object (from categorizeDetail) or an `error` message
// to render instead. Cost → counts → flags → IDs, in that order.
export function DetailChips({
  groups,
  error,
}: {
  groups: DetailGroups;
  error?: string | null;
}) {
  if (error) {
    return <span style={{ color: 'var(--impact)' }}>{error}</span>;
  }
  const isEmpty =
    groups.cost == null &&
    groups.counts.length === 0 &&
    groups.flags.length === 0 &&
    groups.ids.length === 0;
  if (isEmpty) {
    return <span style={{ color: 'var(--text-dim)' }}>—</span>;
  }
  return (
    <div className="pipeline-detail-chips">
      {groups.cost != null && (
        <span className="meta-chip pipeline-cost-chip">{fmtCost(groups.cost)}</span>
      )}
      {groups.counts.map(([k, v]) => (
        <span key={k} className="meta-chip">
          {SHORT_KEYS[k] ?? k}={fmtChipValue(v)}
        </span>
      ))}
      {groups.flags.map(([k, v]) => (
        <span key={k} className="meta-chip">
          {SHORT_KEYS[k] ?? k}={fmtChipValue(v)}
        </span>
      ))}
      {groups.ids.map(([k, v]) => (
        <span key={k} className="meta-chip pipeline-id-chip">
          {ID_LABELS[k] ?? k} #{String(v)}
        </span>
      ))}
    </div>
  );
}
