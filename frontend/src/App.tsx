import { useState, useCallback, useEffect } from 'react'
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
import AdvisorPanel from './components/AdvisorPanel'
import useScreenCapture from './hooks/useScreenCapture'
import AdvisorToast from './components/AdvisorToast'
import ProposalQueuePage, { type ProposalProducerTarget, type OriginFilter } from './components/ProposalQueuePage'
import DocumentIngestPage from './components/DocumentIngestPage'
import IntegrityPage from './components/IntegrityPage'
import GroupLandingPage from './components/GroupLandingPage'

import { fetchTenantConfig, fetchAllTenants } from './config'
import type { TenantConfig, TenantSummary } from './config'
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

type Tab = 'home' | 'explorer' | 'graph' | 'universe' | 'dashboard' | 'layer-heatmap' | 'query' | 'samples' | 'evaluation' | 'eval-runs' | 'refinements' | 'autopilot' | 'proposal-queue' | 'emergent-queue' | 'document-ingest' | 'integrity' | 'synaptic-learning' | 'quality' | 'fairness' | 'performance' | 'pipeline-timing' | 'knowledge-governance' | 'engrams' | 'agents' | 'corvus-feed' | 'corvus-observations' | 'query-landing' | 'autopilot-landing' | 'knowledge-landing' | 'evaluate-landing' | 'history-landing';

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

const IconMonitor = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
  </svg>
);

function buildNavGroups(tenantId: string | undefined): NavGroup[] {
  const groups: NavGroup[] = [];

  if (tenantId === 'corvus-apex') {
    groups.push({
      label: 'Corvus',
      landingKey: 'corvus-feed',
      description: 'Screen capture and observation pipeline',
      icon: IconMonitor,
      items: [
        { key: 'corvus-feed', label: 'Screen Watcher', description: 'Live screen capture feed and OCR pipeline' },
        { key: 'corvus-observations', label: 'Observations', description: 'Review and approve captured observations' },
      ],
    });
  }

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
  const [collapsed, setCollapsed] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set()
  );
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const [advisorOpen, setAdvisorOpen] = useState(false);
  const [advisorUnread, setAdvisorUnread] = useState(0);
  const screenCapture = useScreenCapture();
  const [tenantConfig, setTenantConfig] = useState<TenantConfig | null>(null);
  const [allTenants, setAllTenants] = useState<TenantSummary[]>([]);
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
      fetchAllTenants().then(setAllTenants);
    }
  }, [authStatus]);

  const displayName = tenantConfig?.display_name ?? 'Corvus';
  const otherTenants = allTenants.filter(t => t.tenant_id !== tenantConfig?.tenant_id);

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

  const toggleGroup = useCallback((label: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  // Build nav groups based on tenant (screen watcher only for apex)
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
      <aside className={`sidebar${collapsed ? ' sidebar-collapsed' : ''}`}>
        <div className="sidebar-header">
          <img src="/corvus-logo-128.png" alt="Corvus" className="sidebar-logo" onClick={() => setTab('home')} style={{ cursor: 'pointer' }} />
          {!collapsed && <h1 className="app-title" onClick={() => setTab('home')} style={{ cursor: 'pointer' }}>{displayName}</h1>}
          <button
            className="sidebar-toggle"
            onClick={() => setCollapsed(c => !c)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? '\u25B6' : '\u25C0'}
          </button>
        </div>
        {!collapsed && otherTenants.length > 0 && (
          <div className="tenant-switcher">
            {otherTenants.map(t => (
              <a
                key={t.tenant_id}
                href={t.default_port ? `http://localhost:${t.default_port}` : '#'}
                className="tenant-switcher-link"
                title={`Switch to ${t.display_name}`}
              >
                {t.display_name} &rarr;
              </a>
            ))}
          </div>
        )}
        {!collapsed ? (
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
        ) : (
          <nav className="sidebar-collapsed-nav">
            {navGroups.map(group => (
              <button
                key={group.label}
                className={`sidebar-collapsed-icon-btn${activeGroup === group.label ? ' active' : ''}`}
                onClick={() => setTab(group.landingKey)}
                title={group.label}
              >
                {group.icon}
              </button>
            ))}
          </nav>
        )}
        {/* Project info — always visible, even when collapsed */}
        {!collapsed && (
          <div className="sidebar-info-area" style={{ padding: '8px 12px', fontSize: '11px', color: 'var(--text-muted, #888)', borderTop: '1px solid var(--border, #333)', marginTop: 'auto' }}>
            <div style={{ marginBottom: 2, opacity: 0.7 }}>Running at <strong>http://localhost:8002</strong></div>
            <a href="https://github.com/tylerbvogel-max/Corvus-2.0" target="_blank" rel="noopener noreferrer" style={{ fontFamily: 'monospace', fontSize: '10px', opacity: 0.5, color: 'inherit', textDecoration: 'none' }} onMouseEnter={e => (e.currentTarget.style.opacity = '0.8')} onMouseLeave={e => (e.currentTarget.style.opacity = '0.5')}>github.com/tylerbvogel-max/Corvus-2.0</a>
          </div>
        )}
        {/* Advisor + Settings — always visible, even when collapsed */}
        <div className="sidebar-settings-area">
          <button
            className="sidebar-settings-btn"
            onClick={() => setAdvisorOpen(o => !o)}
            title="Advisor"
            style={{ color: advisorOpen || screenCapture.isCapturing ? 'var(--accent)' : undefined, position: 'relative' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="2" fill="currentColor" />
              <path d="M16.24 7.76a6 6 0 0 1 0 8.49" />
              <path d="M7.76 16.24a6 6 0 0 1 0-8.49" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
              <path d="M4.93 19.07a10 10 0 0 1 0-14.14" />
            </svg>
            {advisorUnread > 0 && (
              <span style={{
                position: 'absolute', top: 1, right: 1,
                width: advisorUnread > 9 ? 16 : 14, height: 14,
                borderRadius: 7, background: '#ef4444',
                color: '#fff', fontSize: '0.55rem', fontWeight: 700,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                lineHeight: 1,
              }}>
                {advisorUnread > 9 ? '9+' : advisorUnread}
              </span>
            )}
          </button>
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
      </aside>

      {/* Settings popup */}
      {themeMenuOpen && (
        <>
          <div className="settings-overlay" onClick={() => setThemeMenuOpen(false)} />
          <div className="settings-popup">
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
        <AdvisorPanel open={advisorOpen} onClose={() => setAdvisorOpen(false)} screenCapture={screenCapture} />
        <AdvisorToast panelOpen={advisorOpen} onOpenPanel={() => setAdvisorOpen(true)} onUnreadChange={setAdvisorUnread} />
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


