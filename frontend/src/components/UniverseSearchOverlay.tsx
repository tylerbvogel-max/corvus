import { useEffect, useMemo, useRef, useState } from 'react';
import { type Graph3DNode } from '../api';
import { DEPT_COLORS } from '../constants';

interface Props {
  neurons: Graph3DNode[];
  onSelect: (nodeId: number) => void;
  onClose: () => void;
}

const MAX_RESULTS = 10;
const CONCEPT_COLOR = '#e879f9';
const LAYER_LABELS = ['Department', 'Role', 'Task', 'System', 'Decision', 'Output'];

/**
 * Fuzzy-search overlay for the 3D Universe (D1).
 *
 * Matches case-insensitive substrings against node labels, with a small
 * preference bump for labels that start with the query. Scores by:
 *   label-startswith → +200
 *   label-contains   → +100 minus index-of
 *   department match → +20
 * Ties broken by invocations desc (frequently-fired neurons first).
 *
 * Keyboard:
 *   ↑ / ↓ / k / j : move cursor
 *   Enter         : select highlighted result
 *   Esc           : close
 */
export function UniverseSearchOverlay({ neurons, onSelect, onClose }: Props) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { setCursor(0); }, [query]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const scored: Array<{ n: Graph3DNode; score: number }> = [];
    for (const n of neurons) {
      const label = (n.label || '').toLowerCase();
      const dept = (n.department || '').toLowerCase();
      let score = 0;
      if (label.startsWith(q)) score += 200;
      else {
        const idx = label.indexOf(q);
        if (idx >= 0) score += 100 - Math.min(idx, 50);
      }
      if (dept.includes(q)) score += 20;
      if (score > 0) scored.push({ n, score });
    }
    scored.sort((a, b) => (b.score - a.score) || (b.n.invocations - a.n.invocations));
    return scored.slice(0, MAX_RESULTS).map(x => x.n);
  }, [neurons, query]);

  function commit(idx: number) {
    const chosen = results[idx];
    if (chosen) onSelect(chosen.id);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (results.length > 0) commit(cursor);
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'j') {
      if (e.key === 'j' && !e.ctrlKey) {
        // 'j' outside an input conflict: only intercept if we're in the overlay root
        // (the input itself will receive 'j' directly — this path is for bare key nav
        // after Tab-ing out of the input, which is uncommon but allowed).
      }
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
      return;
    }
    if (e.key === 'ArrowUp' || e.key === 'k') {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
      return;
    }
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // Only intercept nav keys; letters should type into the input normally.
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === 'Escape') {
      onKeyDown(e as unknown as React.KeyboardEvent<HTMLDivElement>);
    }
  }

  return (
    <div
      role="dialog"
      aria-label="Search knowledge universe"
      aria-modal="true"
      onKeyDown={onKeyDown}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'absolute', inset: 0, zIndex: 100,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        paddingTop: '15vh',
        background: 'rgba(10, 14, 23, 0.55)',
        backdropFilter: 'blur(2px)',
      }}
    >
      <div
        style={{
          width: 480, maxWidth: '90vw',
          background: '#131926ee', border: '1px solid #1e2d4a',
          borderRadius: 10, boxShadow: '0 10px 40px rgba(0,0,0,0.5)',
          backdropFilter: 'blur(10px)',
          overflow: 'hidden',
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
          placeholder="Search neurons by label or department…"
          aria-label="Search query"
          style={{
            width: '100%', boxSizing: 'border-box',
            background: 'transparent', border: 'none', outline: 'none',
            color: '#f8fafc', padding: '16px 20px',
            fontSize: '1rem', fontFamily: 'inherit',
            borderBottom: '1px solid #1e2d4a',
          }}
        />
        <div style={{ maxHeight: 340, overflowY: 'auto' }}>
          {query.trim() === '' ? (
            <div style={{ padding: '14px 20px', color: '#c8d0dc', fontSize: '0.8rem' }}>
              Start typing to search. Use ↑ / ↓ to navigate, Enter to select, Esc to close.
            </div>
          ) : results.length === 0 ? (
            <div style={{ padding: '14px 20px', color: '#c8d0dc', fontSize: '0.8rem' }}>
              No matches among {neurons.length.toLocaleString()} loaded neurons.
            </div>
          ) : (
            results.map((n, i) => {
              const isConcept = n.node_type === 'concept' && n.layer === -1;
              const accent = isConcept ? CONCEPT_COLOR : (DEPT_COLORS[n.department] || '#c8d0dc');
              const active = i === cursor;
              return (
                <div
                  key={n.id}
                  onClick={() => commit(i)}
                  onMouseEnter={() => setCursor(i)}
                  style={{
                    padding: '10px 20px',
                    background: active ? '#1a2136' : 'transparent',
                    borderLeft: `3px solid ${active ? accent : 'transparent'}`,
                    cursor: 'pointer',
                    display: 'flex', flexDirection: 'column', gap: 3,
                  }}
                >
                  <div style={{ color: '#f8fafc', fontSize: '0.88rem', fontWeight: active ? 600 : 500 }}>
                    {n.label}
                  </div>
                  <div style={{ color: '#c8d0dc', fontSize: '0.72rem', display: 'flex', gap: 10 }}>
                    {isConcept ? (
                      <span style={{ color: CONCEPT_COLOR, fontWeight: 600 }}>Concept</span>
                    ) : (
                      <>
                        <span style={{ color: accent }}>{n.department || 'Unknown'}</span>
                        <span>L{n.layer} {LAYER_LABELS[n.layer] || ''}</span>
                      </>
                    )}
                    <span style={{ color: '#c8d0dc88' }}>· {n.invocations} invocations</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div
          style={{
            padding: '8px 20px', fontSize: '0.7rem', color: '#c8d0dc88',
            borderTop: '1px solid #1e2d4a', background: '#0f1420',
            display: 'flex', justifyContent: 'space-between',
          }}
        >
          <span>{results.length > 0 ? `${results.length} of ${neurons.length.toLocaleString()}` : `${neurons.length.toLocaleString()} neurons`}</span>
          <span>↑ ↓ navigate · Enter select · Esc close</span>
        </div>
      </div>
    </div>
  );
}
