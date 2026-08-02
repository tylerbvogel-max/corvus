import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchRefinementHistory } from '../api/knowledge_graph';
import type { NeuronRefinementEntry } from '../types';

// ── Types ────────────────────────────────────────────────────────────

type SortKey = 'created_at' | 'query_id' | 'neuron_id' | 'action' | 'field' | 'reason';
type GroupBy = 'none' | 'action' | 'field' | 'day' | 'week' | 'neuron';

interface TimePreset {
  label: string;
  days: number | null; // null = all time
}

const TIME_PRESETS: TimePreset[] = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: 'All', days: null },
];

interface ColumnDef {
  key: string;
  label: string;
  defaultWidth: number;
  minWidth: number;
}

const COLUMNS: ColumnDef[] = [
  { key: 'created_at', label: 'Date', defaultWidth: 140, minWidth: 90 },
  { key: 'query', label: 'Query', defaultWidth: 200, minWidth: 100 },
  { key: 'neuron', label: 'Neuron', defaultWidth: 180, minWidth: 100 },
  { key: 'action', label: 'Action', defaultWidth: 80, minWidth: 60 },
  { key: 'field', label: 'Field', defaultWidth: 90, minWidth: 60 },
  { key: 'diff', label: 'Old → New', defaultWidth: 320, minWidth: 120 },
  { key: 'reason', label: 'Reason', defaultWidth: 220, minWidth: 100 },
];

// ── Helpers ──────────────────────────────────────────────────────────

function sinceDate(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split('T')[0];
}

function dayKey(iso: string | null): string {
  if (!iso) return 'Unknown';
  return iso.split('T')[0];
}

function weekKey(iso: string | null): string {
  if (!iso) return 'Unknown';
  const d = new Date(iso);
  const jan1 = new Date(d.getFullYear(), 0, 1);
  const week = Math.ceil(((d.getTime() - jan1.getTime()) / 86400000 + jan1.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, '0')}`;
}

function groupLabel(groupBy: GroupBy, entry: NeuronRefinementEntry): string {
  if (groupBy === 'action') return entry.action;
  if (groupBy === 'field') return entry.field || '(none)';
  if (groupBy === 'day') return dayKey(entry.created_at ?? null);
  if (groupBy === 'week') return weekKey(entry.created_at ?? null);
  if (groupBy === 'neuron') return entry.neuron_label || `#${entry.neuron_id}`;
  return '';
}

function compareFn(key: SortKey, a: NeuronRefinementEntry, b: NeuronRefinementEntry): number {
  if (key === 'created_at') return (a.created_at || '').localeCompare(b.created_at || '');
  if (key === 'query_id') return (a.query_id ?? 0) - (b.query_id ?? 0);
  if (key === 'neuron_id') return a.neuron_id - b.neuron_id;
  if (key === 'action') return a.action.localeCompare(b.action);
  if (key === 'field') return (a.field || '').localeCompare(b.field || '');
  if (key === 'reason') return (a.reason || '').localeCompare(b.reason || '');
  return 0;
}

// ── Component ────────────────────────────────────────────────────────

export default function RefinementHistory() {
  const [entries, setEntries] = useState<NeuronRefinementEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [timePreset, setTimePreset] = useState<number | null>(30);
  const [customSince, setCustomSince] = useState('');
  const [customUntil, setCustomUntil] = useState('');
  const [actionFilter, setActionFilter] = useState('all');
  const [fieldFilter, setFieldFilter] = useState('all');
  const [search, setSearch] = useState('');

  // Table controls
  const [sortKey, setSortKey] = useState<SortKey>('created_at');
  const [sortAsc, setSortAsc] = useState(false);
  const [groupBy, setGroupBy] = useState<GroupBy>('none');
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(
    () => Object.fromEntries(COLUMNS.map(c => [c.key, c.defaultWidth]))
  );
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());

  // Resize state
  const resizingRef = useRef<{ key: string; startX: number; startW: number } | null>(null);

  // ── Fetch data ──────────────────────────────────────────────────

  const loadData = useCallback(() => {
    setLoading(true);
    setError(null);
    const params: Record<string, string | number> = {};
    if (timePreset !== null) {
      params.since = sinceDate(timePreset);
    } else if (customSince) {
      params.since = customSince;
    }
    if (customUntil) params.until = customUntil;
    fetchRefinementHistory(Object.keys(params).length > 0 ? params as any : undefined)
      .then(setEntries)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [timePreset, customSince, customUntil]);

  useEffect(() => { loadData(); }, [loadData]);

  // ── Filter + sort ───────────────────────────────────────────────

  const filtered = entries
    .filter(e => actionFilter === 'all' || e.action === actionFilter)
    .filter(e => fieldFilter === 'all' || (e.field || '') === fieldFilter)
    .filter(e => {
      if (!search) return true;
      const s = search.toLowerCase();
      return (
        (e.neuron_label || '').toLowerCase().includes(s) ||
        (e.query_snippet || '').toLowerCase().includes(s) ||
        (e.reason || '').toLowerCase().includes(s) ||
        String(e.neuron_id).includes(s)
      );
    })
    .sort((a, b) => {
      const cmp = compareFn(sortKey, a, b);
      return sortAsc ? cmp : -cmp;
    });

  // Unique values for filter dropdowns
  const actions = [...new Set(entries.map(e => e.action))].sort();
  const fields = [...new Set(entries.map(e => e.field).filter(Boolean))].sort() as string[];

  // ── Grouping ────────────────────────────────────────────────────

  const groups: { label: string; entries: NeuronRefinementEntry[] }[] = [];
  if (groupBy === 'none') {
    groups.push({ label: '', entries: filtered });
  } else {
    const map = new Map<string, NeuronRefinementEntry[]>();
    for (const e of filtered) {
      const key = groupLabel(groupBy, e);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(e);
    }
    // Sort groups: date groups descending, others alphabetical
    const sorted = [...map.entries()].sort((a, b) => {
      if (groupBy === 'day' || groupBy === 'week') return b[0].localeCompare(a[0]);
      return a[0].localeCompare(b[0]);
    });
    for (const [label, ents] of sorted) {
      groups.push({ label, entries: ents });
    }
  }

  // ── Summary stats ───────────────────────────────────────────────

  const createCount = filtered.filter(e => e.action === 'create').length;
  const updateCount = filtered.filter(e => e.action === 'update').length;
  const uniqueNeurons = new Set(filtered.map(e => e.neuron_id)).size;

  // ── Column resize handlers ──────────────────────────────────────

  function onResizeStart(e: React.MouseEvent, key: string) {
    e.preventDefault();
    resizingRef.current = { key, startX: e.clientX, startW: columnWidths[key] };

    const onMove = (ev: MouseEvent) => {
      if (!resizingRef.current) return;
      const col = COLUMNS.find(c => c.key === resizingRef.current!.key)!;
      const delta = ev.clientX - resizingRef.current.startX;
      const newW = Math.max(col.minWidth, resizingRef.current.startW + delta);
      setColumnWidths(prev => ({ ...prev, [resizingRef.current!.key]: newW }));
    };
    const onUp = () => {
      resizingRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  // ── Sort handlers ───────────────────────────────────────────────

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  }

  function sortArrow(key: SortKey) {
    if (sortKey !== key) return '';
    return sortAsc ? ' \u25B2' : ' \u25BC';
  }

  function toggleGroupCollapse(label: string) {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }

  // ── Render ──────────────────────────────────────────────────────

  return (
    <div className="dashboard" style={{ maxWidth: 1600 }}>
      <h2 style={{ marginBottom: 12, fontSize: '1.25rem' }}>Refinement History</h2>

      {/* Summary bar */}
      <div className="refine-summary-bar">
        <span className="refine-stat">{filtered.length} refinements</span>
        <span className="refine-stat"><span className="action-badge action-create">create</span> {createCount}</span>
        <span className="refine-stat"><span className="action-badge action-update">update</span> {updateCount}</span>
        <span className="refine-stat">{uniqueNeurons} neurons affected</span>
      </div>

      {/* Controls row */}
      <div className="refine-controls">
        {/* Time presets */}
        <div className="refine-control-group">
          <label className="refine-control-label">Time</label>
          <div className="refine-btn-group">
            {TIME_PRESETS.map(p => (
              <button
                key={p.label}
                className={`refine-preset-btn${timePreset === p.days ? ' active' : ''}`}
                onClick={() => { setTimePreset(p.days); setCustomSince(''); setCustomUntil(''); }}
              >
                {p.label}
              </button>
            ))}
            <button
              className={`refine-preset-btn${timePreset === -1 ? ' active' : ''}`}
              onClick={() => setTimePreset(-1)}
            >
              Custom
            </button>
          </div>
          {timePreset === -1 && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 8 }}>
              <input type="date" className="refine-date-input" value={customSince} onChange={e => { setCustomSince(e.target.value); setTimePreset(-1); }} />
              <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>to</span>
              <input type="date" className="refine-date-input" value={customUntil} onChange={e => { setCustomUntil(e.target.value); setTimePreset(-1); }} />
              <button className="refine-preset-btn" onClick={loadData} style={{ fontWeight: 600 }}>Go</button>
            </div>
          )}
        </div>

        {/* Filters */}
        <div className="refine-control-group">
          <label className="refine-control-label">Filter</label>
          <select className="refine-select" value={actionFilter} onChange={e => setActionFilter(e.target.value)}>
            <option value="all">All actions</option>
            {actions.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
          <select className="refine-select" value={fieldFilter} onChange={e => setFieldFilter(e.target.value)}>
            <option value="all">All fields</option>
            {fields.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
          <input
            className="refine-search"
            type="text"
            placeholder="Search neurons, queries, reasons..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {/* Group by */}
        <div className="refine-control-group">
          <label className="refine-control-label">Group</label>
          <select className="refine-select" value={groupBy} onChange={e => setGroupBy(e.target.value as GroupBy)}>
            <option value="none">No grouping</option>
            <option value="day">By day</option>
            <option value="week">By week</option>
            <option value="action">By action</option>
            <option value="field">By field</option>
            <option value="neuron">By neuron</option>
          </select>
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="loading">Loading refinements...</div>
      ) : error ? (
        <div className="error-msg">{error}</div>
      ) : filtered.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', padding: 16 }}>No refinements match the current filters.</p>
      ) : (
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table className="refine-table" style={{ tableLayout: 'fixed', width: Object.values(columnWidths).reduce((a, b) => a + b, 0) }}>
            <colgroup>
              {COLUMNS.map(c => (
                <col key={c.key} style={{ width: columnWidths[c.key] }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {COLUMNS.map(col => {
                  const sortable: SortKey[] = ['created_at', 'query_id', 'neuron_id', 'action', 'field', 'reason'];
                  const sk = col.key === 'query' ? 'query_id' : col.key === 'neuron' ? 'neuron_id' : col.key === 'diff' ? null : col.key as SortKey;
                  const isSortable = sk && sortable.includes(sk);
                  return (
                    <th
                      key={col.key}
                      className={isSortable ? 'refine-th-sortable' : undefined}
                      onClick={isSortable ? () => handleSort(sk) : undefined}
                      style={{ position: 'relative' }}
                    >
                      {col.label}{isSortable ? sortArrow(sk) : ''}
                      <span
                        className="refine-col-resize"
                        onMouseDown={e => onResizeStart(e, col.key)}
                      />
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {groups.map(group => (
                <GroupRows
                  key={group.label || '__all'}
                  label={group.label}
                  entries={group.entries}
                  showHeader={groupBy !== 'none'}
                  collapsed={collapsedGroups.has(group.label)}
                  onToggle={() => toggleGroupCollapse(group.label)}
                  colCount={COLUMNS.length}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Group rows sub-component ──────────────────────────────────────

function GroupRows({ label, entries, showHeader, collapsed, onToggle, colCount }: {
  label: string;
  entries: NeuronRefinementEntry[];
  showHeader: boolean;
  collapsed: boolean;
  onToggle: () => void;
  colCount: number;
}) {
  return (
    <>
      {showHeader && (
        <tr className="refine-group-header" onClick={onToggle}>
          <td colSpan={colCount}>
            <span className="refine-group-chevron">{collapsed ? '\u25B8' : '\u25BE'}</span>
            <strong>{label}</strong>
            <span className="refine-group-count">{entries.length}</span>
          </td>
        </tr>
      )}
      {!collapsed && entries.map(e => (
        <tr key={e.id}>
          <td className="refine-cell-date">
            {e.created_at ? new Date(e.created_at).toLocaleString() : '\u2014'}
          </td>
          <td className="refine-cell-query">
            <span className="refine-id">#{e.query_id}</span>
            {e.query_snippet && <div className="refine-snippet">{e.query_snippet}</div>}
          </td>
          <td className="refine-cell-neuron">
            <span className="refine-neuron-id">#{e.neuron_id}</span>
            {e.neuron_label && <div className="refine-neuron-label">{e.neuron_label}</div>}
          </td>
          <td>
            <span className={`action-badge action-${e.action}`}>{e.action}</span>
          </td>
          <td>
            {e.field ? <span className="refine-field">{e.field}</span> : '\u2014'}
          </td>
          <td className="refine-cell-diff">
            {e.action === 'update' ? (
              <div className="refine-diff-row">
                <span className="diff-old">{e.old_value}</span>
                <span className="refine-arrow">{'\u2192'}</span>
                <span className="diff-new">{e.new_value}</span>
              </div>
            ) : (
              <span className="diff-new">{e.new_value}</span>
            )}
          </td>
          <td className="refine-cell-reason">{e.reason || '\u2014'}</td>
        </tr>
      ))}
    </>
  );
}
