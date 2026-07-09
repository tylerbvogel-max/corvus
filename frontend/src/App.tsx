import { useState, useCallback, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import Explorer from './components/Explorer'
import Dashboard from './components/Dashboard'
import QueryLab from './components/QueryLab'
import EvaluationPage from './components/EvaluationPage'
import EvalRunsPage from './components/EvalRunsPage'
import RefinementHistory from './components/RefinementHistory'
import AutopilotPage from './components/AutopilotPage'
import CirclePacking from './components/CirclePacking'
import SampleQueries from './components/SampleQueries'
import QualityPage from './components/QualityPage'
import FairnessPage from './components/FairnessPage'
import PerformancePage from './components/PerformancePage'
import PipelineTimingPage from './components/PipelineTimingPage'
import EmergentQueuePage from './components/EmergentQueuePage'
import SynapticLearningPage from './components/SynapticLearningPage'
import LayerHeatmap from './components/LayerHeatmap'
import NeuronUniverse from './components/NeuronUniverse'
import KnowledgeGovernancePage from './components/KnowledgeGovernancePage'
import HomePage from './components/HomePage'
import SystemUseBanner from './components/SystemUseBanner'
import EngramPage from './components/EngramPage'
import AgentsPage from './components/AgentsPage'
import ProposalQueuePage, { type ProposalProducerTarget, type OriginFilter } from './components/ProposalQueuePage'
import DocumentIngestPage from './components/DocumentIngestPage'
import IntegrityPage from './components/IntegrityPage'
import GroupLandingPage from './components/GroupLandingPage'

import { fetchTenantConfig } from './config'
import type { TenantConfig } from './config'
import { checkAccess, setAccessKey, getAccessKey } from './auth'
import { fetchProposalStats } from './api'

type OriginKey = 'autopilot' | 'integrity' | 'document' | 'emergent' | 'manual';

// Which tab each origin navigates back to when a Proposal Queue row's
// reverse link is clicked. Kept in App.tsx because the producer-page
// components don't know about each other.
const ORIGIN_TO_TAB: Record<OriginKey, Tab> = {
  autopilot: 'autopilot',
  integrity: 'integrity',
  document: 'document-ingest',
  emergent: 'emergent-queue',
  manual: 'proposal-queue',
};

// Nav-item key → origin it represents in the pending-proposal count.
// Proposal Queue itself aggregates all pending; the four producer tabs
// each show their own origin's pending count.
const TAB_TO_ORIGIN: Partial<Record<Tab, OriginKey | 'all'>> = {
  'autopilot': 'autopilot',
  'integrity': 'integrity',
  'document-ingest': 'document',
  'emergent-queue': 'emergent',
  'proposal-queue': 'all',
};

type Tab = 'home' | 'explorer' | 'graph' | 'universe' | 'dashboard' | 'layer-heatmap' | 'query' | 'samples' | 'evaluation' | 'eval-runs' | 'refinements' | 'autopilot' | 'proposal-queue' | 'emergent-queue' | 'document-ingest' | 'integrity' | 'synaptic-learning' | 'quality' | 'fairness' | 'performance' | 'pipeline-timing' | 'knowledge-governance' | 'engrams' | 'agents' | 'query-landing' | 'autopilot-landing' | 'knowledge-landing' | 'evaluate-landing' | 'history-landing';

type Theme = 'corvus-native' | 'corvus-dark' | 'corvus-light' | 'high-contrast' | 'colorblind';

const THEME_LABELS: Record<Theme, string> = {
  'corvus-native': 'Corvus Native',
  'corvus-dark': 'Corvus',
  'corvus-light': 'Corvus Light',
  'high-contrast': 'High Contrast',
  'colorblind': 'Colorblind',
};

interface NavItem {
  key: Tab;
  label: string;
  description: string;
  className?: string;
  labelColor?: string;
}

interface NavGroup {
  label: string;
  landingKey: Tab;
  description: string;
  icon: ReactNode;
  items: NavItem[];
}

// --- Group icons (16x16, stroke-based, Feather style) ---

const IconTerminal = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" />
  </svg>
);

const IconCompass = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" /><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
  </svg>
);

const IconNetwork = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="5" r="3" /><circle cx="5" cy="19" r="3" /><circle cx="19" cy="19" r="3" />
    <line x1="12" y1="8" x2="5" y2="16" /><line x1="12" y1="8" x2="19" y2="16" />
  </svg>
);

const IconClipboard = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" />
    <rect x="9" y="3" width="6" height="4" rx="1" /><path d="M9 14l2 2 4-4" />
  </svg>
);

const IconClock = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
  </svg>
);

function buildNavGroups(_tenantId: string | undefined): NavGroup[] {
  const groups: NavGroup[] = [];

  groups.push(
    {
      label: 'Query',
      landingKey: 'query-landing',
      description: 'Run and test queries against the neuron graph',
      icon: IconTerminal,
      items: [
        { key: 'query', label: 'Query Lab', description: 'Run queries against the neuron graph' },
        { key: 'samples', label: 'Samples', description: 'Pre-built queries for testing and demos' },
      ],
    },
    {
      label: 'Autopilot',
      landingKey: 'autopilot-landing',
      description: 'Automated gap detection and staged improvements',
      icon: IconCompass,
      items: [
        { key: 'autopilot', label: 'Autopilot', description: 'Automated gap detection and improvement cycles' },
        { key: 'proposal-queue', label: 'Proposal Queue', description: 'Review and approve autopilot proposals' },
        { key: 'emergent-queue', label: 'Emergent Queue', description: 'Unresolved patterns awaiting classification' },
        { key: 'document-ingest', label: 'Document Ingest', description: 'Upload documents for bulk knowledge extraction' },
        { key: 'integrity', label: 'Integrity', description: 'Graph consistency audits, agents, and findings' },
      ],
    },
    {
      label: 'Knowledge',
      landingKey: 'knowledge-landing',
      description: 'Browse and visualize the neuron graph',
      icon: IconNetwork,
      items: [
        { key: 'explorer', label: 'Explorer', description: 'Browse and edit individual neurons' },
        { key: 'engrams', label: 'Engrams', description: 'Source documents linked to the graph' },
        { key: 'graph', label: 'Graph', description: 'Circle-packing visualization of the hierarchy' },
        { key: 'universe', label: '3D Universe', description: 'Three-dimensional neuron network view' },
        { key: 'layer-heatmap', label: 'Layer Heatmap', description: 'Activity heatmap across graph layers' },
        { key: 'agents', label: 'Agents', description: 'Autonomous maintenance agents that curate the graph' },
      ],
    },
    {
      label: 'Evaluate',
      landingKey: 'evaluate-landing',
      description: 'System health, quality, and compliance metrics',
      icon: IconClipboard,
      items: [
        { key: 'dashboard', label: 'Dashboard', description: 'Aggregate statistics and system overview' },
        { key: 'knowledge-governance', label: 'Governance', description: 'Knowledge governance and compliance metrics' },
        { key: 'quality', label: 'Quality', description: 'Response quality scoring and trends' },
        { key: 'performance', label: 'Performance', description: 'Pipeline latency and throughput metrics' },
        { key: 'pipeline-timing', label: 'Pipeline Timing', description: 'Per-stage latency stats, estimate-vs-actual, and drift over time' },
        { key: 'fairness', label: 'Fairness', description: 'Bias detection across departments and roles' },
        { key: 'evaluation', label: 'Evaluation', description: 'Per-query evaluation scores and history' },
        { key: 'eval-runs', label: 'Eval Runs', description: 'Immutable eval artifacts — certify a run to stamp /v1/query' },
      ],
    },
    {
      label: 'History',
      landingKey: 'history-landing',
      description: 'How the graph has evolved over time',
      icon: IconClock,
      items: [
        { key: 'refinements', label: 'Refinements', description: 'History of neuron updates and changes' },
        { key: 'synaptic-learning', label: 'Synaptic Learning', description: 'Learned patterns from query feedback' },
      ],
    },
  );

  return groups;
}

function getInitialTheme(): Theme {
  const saved = localStorage.getItem('corvus-theme');
  if (saved && saved in THEME_LABELS) {
    return saved as Theme;
  }
  return 'corvus-native';
}

export default function App() {
  const [tab, setTab] = useState<Tab>('home');
  const [explorerNeuronId, setExplorerNeuronId] = useState<number | null>(null);
  const [proposedByOrigin, setProposedByOrigin] = useState<Record<string, number>>({});
  const [totalProposed, setTotalProposed] = useState(0);
  const [queueInitialOrigin, setQueueInitialOrigin] = useState<OriginFilter | undefined>(undefined);
  // Floating nav: collapsed = logo-only pill; position is draggable and
  // persisted. Defaults just off the top-left corner.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('corvus-nav-collapsed') === '1');
  const [navPos, setNavPos] = useState<{ x: number; y: number }>(() => {
    try {
      const p = JSON.parse(localStorage.getItem('corvus-nav-pos') ?? '');
      if (typeof p?.x === 'number' && typeof p?.y === 'number') return p;
    } catch { /* fall through to default */ }
    return { x: 18, y: 18 };
  });
  const navRef = useRef<HTMLElement>(null);
  const navPosRef = useRef(navPos);
  navPosRef.current = navPos;
  // True while the current pointer interaction moved the panel — used to
  // suppress the click that fires after a drag ends on the same element.
  const navDragMovedRef = useRef(false);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set()
  );
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const [tenantConfig, setTenantConfig] = useState<TenantConfig | null>(null);
  const [authStatus, setAuthStatus] = useState<'checking' | 'open' | 'valid' | 'needs_key'>('checking');
  const [keyInput, setKeyInput] = useState('');
  const [keyError, setKeyError] = useState(false);

  // Check auth on mount
  useEffect(() => {
    checkAccess().then(status => {
      if (status === 'invalid' && !getAccessKey()) setAuthStatus('needs_key');
      else if (status === 'invalid') setAuthStatus('needs_key');
      else setAuthStatus(status);
    });
  }, []);

  // Fetch tenant config + all tenants once authed
  useEffect(() => {
    if (authStatus === 'open' || authStatus === 'valid') {
      fetchTenantConfig().then(setTenantConfig);
    }
  }, [authStatus]);

  const displayName = tenantConfig?.display_name ?? 'Corvus';

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('corvus-theme', theme);
  }, [theme]);

  // Poll proposal stats so each producer nav item can show its pending count.
  // 30s cadence: fast enough to feel live after an approval, slow enough not
  // to hammer the backend. Bounded — one HTTP call per tick.
  useEffect(() => {
    if (authStatus !== 'open' && authStatus !== 'valid') return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchProposalStats();
        if (cancelled) return;
        setProposedByOrigin(s.proposed_by_origin ?? {});
        setTotalProposed(s.proposed ?? 0);
      } catch {
        // Swallow — stats are advisory; a failed poll just leaves stale counts.
      }
    };
    void tick();
    const handle = window.setInterval(tick, 30_000);
    return () => { cancelled = true; window.clearInterval(handle); };
  }, [authStatus]);

  // Reverse deep-link: Proposal Queue row → producer page.
  const navigateToProducer = useCallback((target: ProposalProducerTarget) => {
    const origin = target.origin as OriginKey;
    const nextTab = ORIGIN_TO_TAB[origin] ?? 'proposal-queue';
    setTab(nextTab);
    // Future: stash target.autopilot_run_id / finding_id / scan_id in
    // a per-page focus state so the producer page can highlight the row.
    // For now the tab switch alone is the deep-link payload.
  }, []);

  // Forward deep-link: producer nav badge → Proposal Queue filtered to origin.
  const navigateToFilteredQueue = useCallback((origin: OriginKey | 'all') => {
    setQueueInitialOrigin(origin === 'all' ? undefined : (origin as OriginFilter));
    setTab('proposal-queue');
  }, []);

  function setTheme(t: Theme) {
    setThemeState(t);
    setThemeMenuOpen(false);
  }

  function navigateToNeuron(id: number) {
    setExplorerNeuronId(id);
    setTab('explorer');
  }

  // Keep the floating nav fully on-screen (8px margin all around).
  const clampNavPos = useCallback((p: { x: number; y: number }) => {
    const el = navRef.current;
    const w = el?.offsetWidth ?? 220;
    const h = el?.offsetHeight ?? 52;
    const x = Math.max(8, Math.min(p.x, window.innerWidth - w - 8));
    const y = Math.max(8, Math.min(p.y, window.innerHeight - h - 8));
    return x === p.x && y === p.y ? p : { x, y };
  }, []);

  const startNavDrag = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const startX = e.clientX, startY = e.clientY;
    const origin = navPosRef.current;
    navDragMovedRef.current = false;
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      // 4px dead zone so ordinary clicks on the handle never jiggle the panel
      if (!navDragMovedRef.current && Math.hypot(dx, dy) < 4) return;
      navDragMovedRef.current = true;
      ev.preventDefault();
      setNavPos(clampNavPos({ x: origin.x + dx, y: origin.y + dy }));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [clampNavPos]);

  useEffect(() => {
    localStorage.setItem('corvus-nav-pos', JSON.stringify(navPos));
  }, [navPos]);

  useEffect(() => {
    localStorage.setItem('corvus-nav-collapsed', collapsed ? '1' : '0');
    // Panel size just changed (pill ↔ full panel): re-clamp to the viewport.
    setNavPos(p => clampNavPos(p));
  }, [collapsed, clampNavPos]);

  useEffect(() => {
    const onResize = () => setNavPos(p => clampNavPos(p));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [clampNavPos]);

  const toggleGroup = useCallback((label: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  // Build nav groups based on tenant
  const navGroups = buildNavGroups(tenantConfig?.tenant_id);
  const activeGroup = navGroups.find(g => g.landingKey === tab || g.items.some(i => i.key === tab))?.label;

  // Auth gate
  if (authStatus === 'checking') {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--text-dim)' }}>Loading...</div>;
  }

  if (authStatus === 'needs_key') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg)' }}>
        <div style={{ textAlign: 'center', maxWidth: 360 }}>
          <img src="/corvus-logo.png" alt="Corvus" style={{ width: 64, height: 64, marginBottom: 16, opacity: 0.8 }} />
          <h2 style={{ color: 'var(--text)', fontSize: 18, margin: '0 0 8px' }}>Corvus Access</h2>
          <p style={{ color: 'var(--text-dim)', fontSize: 13, margin: '0 0 20px' }}>Enter your access key to continue.</p>
          <form onSubmit={e => {
            e.preventDefault();
            setAccessKey(keyInput);
            setKeyError(false);
            checkAccess().then(status => {
              if (status === 'valid' || status === 'open') setAuthStatus(status);
              else { setKeyError(true); setKeyInput(''); }
            });
          }}>
            <input
              type="password"
              value={keyInput}
              onChange={e => setKeyInput(e.target.value)}
              placeholder="Access key"
              autoFocus
              style={{
                width: '100%', padding: '10px 14px', fontSize: 14,
                background: 'var(--bg-input)', border: `1px solid ${keyError ? '#ef4444' : 'var(--border)'}`,
                borderRadius: 8, color: 'var(--text)', outline: 'none',
                marginBottom: 12, fontFamily: 'monospace',
              }}
            />
            {keyError && <p style={{ color: '#ef4444', fontSize: 12, margin: '0 0 12px' }}>Invalid access key</p>}
            <button type="submit" style={{
              width: '100%', padding: '10px', fontSize: 14, fontWeight: 600,
              background: 'var(--accent, #c87533)', color: '#fff', border: 'none',
              borderRadius: 8, cursor: 'pointer',
            }}>
              Enter
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="app app-sidebar-layout">
      <SystemUseBanner />
      <aside
        ref={navRef}
        className={`sidebar${collapsed ? ' sidebar-pill' : ''}`}
        style={{ left: navPos.x, top: navPos.y }}
      >
        {collapsed ? (
          /* Logo pill: drag to move, click to expand */
          <button
            className="sidebar-pill-btn"
            onPointerDown={startNavDrag}
            onClick={() => { if (!navDragMovedRef.current) setCollapsed(false); }}
            title="Open navigation (drag to move)"
          >
            <img src="/corvus-logo-128.png" alt="Corvus" className="sidebar-logo" draggable={false} />
          </button>
        ) : (
          <>
        <div className="sidebar-header" onPointerDown={startNavDrag} title="Drag to move">
          <img
            src="/corvus-logo-128.png"
            alt="Corvus"
            className="sidebar-logo"
            draggable={false}
            onClick={() => { if (!navDragMovedRef.current) setTab('home'); }}
            style={{ cursor: 'pointer' }}
          />
          <h1 className="app-title" onClick={() => { if (!navDragMovedRef.current) setTab('home'); }} style={{ cursor: 'pointer' }}>{displayName}</h1>
          <button
            className="sidebar-toggle"
            onClick={() => { if (!navDragMovedRef.current) setCollapsed(true); }}
            title="Collapse to logo"
          >
            {'\u2212'}
          </button>
        </div>
          <nav className="sidebar-nav">
            {navGroups.map(group => (
              <div key={group.label} className={`sidebar-group${activeGroup === group.label ? ' sidebar-group-active' : ''}`}>
                <button
                  className="sidebar-group-header"
                  onClick={() => toggleGroup(group.label)}
                >
                  <span className="sidebar-chevron">{expandedGroups.has(group.label) ? '\u25BE' : '\u25B8'}</span>
                  <span>{group.label}</span>
                </button>
                {expandedGroups.has(group.label) && (
                  <div className="sidebar-group-items">
                    {group.items.map(item => {
                      const originForTab = TAB_TO_ORIGIN[item.key];
                      const count = originForTab === 'all'
                        ? totalProposed
                        : (originForTab ? (proposedByOrigin[originForTab] ?? 0) : 0);
                      return (
                        <button
                          key={item.key}
                          className={`sidebar-item${tab === item.key ? ' active' : ''}${item.className ? ' ' + item.className : ''}`}
                          onClick={() => {
                            // Click body of nav item goes to the page itself. Badge
                            // has its own click handler (see below) that deep-links
                            // to the filtered Proposal Queue.
                            setQueueInitialOrigin(undefined);
                            setTab(item.key);
                          }}
                          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}
                        >
                          <span style={{ color: item.labelColor }}>{item.label}</span>
                          {count > 0 && item.key !== 'proposal-queue' && originForTab && originForTab !== 'all' && (
                            <span
                              onClick={(ev) => {
                                ev.stopPropagation();
                                navigateToFilteredQueue(originForTab);
                              }}
                              title={`${count} pending in Proposal Queue — click to filter`}
                              style={{
                                fontSize: 10, fontWeight: 700,
                                padding: '1px 6px', borderRadius: 10,
                                background: 'var(--accent, #c87533)', color: '#fff',
                                minWidth: 18, textAlign: 'center',
                                cursor: 'pointer',
                              }}
                            >
                              {count}
                            </span>
                          )}
                          {count > 0 && item.key === 'proposal-queue' && (
                            <span
                              title={`${count} pending proposals`}
                              style={{
                                fontSize: 10, fontWeight: 700,
                                padding: '1px 6px', borderRadius: 10,
                                background: 'var(--accent, #c87533)', color: '#fff',
                                minWidth: 18, textAlign: 'center',
                              }}
                            >
                              {count}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </nav>
        {/* Settings — pinned to the bottom of the expanded panel */}
        <div className="sidebar-settings-area">
          <button
            className="sidebar-settings-btn"
            onClick={() => setThemeMenuOpen(o => !o)}
            title="Settings"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        </div>
          </>
        )}
      </aside>

      {/* Settings popup */}
      {themeMenuOpen && (
        <>
          <div className="settings-overlay" onClick={() => setThemeMenuOpen(false)} />
          {/* Anchored just below the floating nav panel, clamped on-screen */}
          <div
            className="settings-popup"
            style={(() => {
              const r = navRef.current?.getBoundingClientRect();
              if (!r) return undefined;
              return {
                left: Math.max(8, Math.min(r.left, window.innerWidth - 252)),
                top: Math.max(8, Math.min(r.bottom + 8, window.innerHeight - 300)),
                bottom: 'auto',
              };
            })()}
          >
            <div className="settings-popup-header">
              <span>Settings</span>
              <button className="settings-popup-close" onClick={() => setThemeMenuOpen(false)}>&times;</button>
            </div>
            <div className="settings-popup-section">
              <label className="settings-label">Theme</label>
              <div className="settings-theme-options">
                {(Object.keys(THEME_LABELS) as Theme[]).map(t => (
                  <button
                    key={t}
                    className={`settings-theme-btn${theme === t ? ' active' : ''}`}
                    onClick={() => setTheme(t)}
                  >
                    <span className={`settings-theme-swatch settings-theme-swatch--${t}`} />
                    <span>{THEME_LABELS[t]}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
      <main className="app-main">
        {tab === 'home' && <HomePage onNavigate={k => setTab(k as Tab)} />}
        {tab === 'explorer' && <Explorer navigateToNeuronId={explorerNeuronId} onNavigateHandled={() => setExplorerNeuronId(null)} />}
        {tab === 'engrams' && <EngramPage />}
        {tab === 'agents' && <AgentsPage />}
        {tab === 'graph' && <CirclePacking />}
        {tab === 'universe' && <NeuronUniverse />}
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'layer-heatmap' && <LayerHeatmap />}
        <div style={{ display: tab === 'query' ? 'contents' : 'none' }}><QueryLab onNavigateToNeuron={navigateToNeuron} /></div>
        {tab === 'evaluation' && <EvaluationPage />}
        {tab === 'eval-runs' && <EvalRunsPage />}
        {tab === 'refinements' && <RefinementHistory />}
        {tab === 'samples' && <SampleQueries />}
        {tab === 'autopilot' && <AutopilotPage />}
        {tab === 'proposal-queue' && (
          <ProposalQueuePage
            initialOriginFilter={queueInitialOrigin}
            onNavigateToProducer={navigateToProducer}
          />
        )}
        {tab === 'emergent-queue' && <EmergentQueuePage />}
        {tab === 'document-ingest' && <DocumentIngestPage />}
        {tab === 'integrity' && <IntegrityPage />}
        {tab === 'synaptic-learning' && <SynapticLearningPage />}
        {tab === 'quality' && <QualityPage />}
        {tab === 'fairness' && <FairnessPage />}
        {tab === 'performance' && <PerformancePage />}
        {tab === 'pipeline-timing' && <PipelineTimingPage />}
        {tab === 'knowledge-governance' && <KnowledgeGovernancePage />}
        {navGroups.map(group => (
          tab === group.landingKey && (
            <GroupLandingPage
              key={group.landingKey}
              title={group.label}
              icon={group.icon}
              description={group.description}
              items={group.items.map(i => ({ key: i.key, label: i.label, description: i.description }))}
              onNavigate={k => setTab(k as Tab)}
            />
          )
        ))}
      </main>
    </div>
  )
}


