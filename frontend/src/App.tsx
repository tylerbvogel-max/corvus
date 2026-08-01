import { useState, useCallback, useEffect, useRef, useMemo, lazy, Suspense } from 'react'
import type { ReactNode } from 'react'
// Imported through Vite (not public/) so the served URL carries a content
// fingerprint — logo swaps bust the browser cache without manual refreshes
// (ChromeOS Chrome never revalidates same-URL images).
import corvusLogo from './assets/corvus-logo.png'
import corvusLogo128 from './assets/corvus-logo-128.png'
import AppWindow, { MIN_W, MIN_H, type WinState, type WinRect } from './components/AppWindow'
import AsciiWake from './components/AsciiWake'
import ChatHistoryWindow from './components/ChatHistoryWindow'
import DemoHelper, { OPEN_WINDOW_EVENT, START_TOUR_EVENT } from './components/DemoHelper'
import MobileShell, { useIsMobile } from './components/MobileShell'
import NeuronGraphWindow from './components/NeuronGraphWindow'
import { CHAT_STARTED_EVENT, CHAT_NEW_EVENT, CHAT_LOAD_SESSION_EVENT } from './chatBus'
import { SingleAgentPane, friendlyName } from './components/AgentsPage'
import WakeSettingsPanel from './components/WakeSettingsPanel'
import Explorer from './components/Explorer'
import QueryLab from './components/QueryLab'
import MindMetricsPage from './components/MindMetricsPage'
import ArchitecturePage from './components/ArchitecturePage'
import MindSessionsPage from './components/MindSessionsPage'
import MindInboxPage from './components/MindInboxPage'
import MindSkillsPage from './components/MindSkillsPage'
import EvaluationPage from './components/EvaluationPage'
import EvalRunsPage from './components/EvalRunsPage'
import RefinementHistory from './components/RefinementHistory'
import CirclePacking from './components/CirclePacking'
import SampleQueries from './components/SampleQueries'
import QualityPage from './components/QualityPage'
import FairnessPage from './components/FairnessPage'
import PerformancePage from './components/PerformancePage'
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
import RoadmapLedgersPage from './components/RoadmapLedgersPage'
import NexusLabPage from './components/NexusLabPage'
import OracleFunnelLabPage from './components/OracleFunnelLabPage'

import { fetchTenantConfig } from './config'
import type { Capability, TenantConfig } from './config'
import { checkAccess, setAccessKey, getAccessKey } from './auth'
import { fetchProposalStats } from './api'

// BYOK setup pill for public demo builds only — the statically-false
// condition removes the chunk from normal builds.
const DemoKeyWizard = import.meta.env.VITE_DEMO === '1'
  ? lazy(() => import('./components/DemoKeyWizard'))
  : null;

type OriginKey = 'autopilot' | 'integrity' | 'document' | 'emergent' | 'manual';

// Which tab each origin navigates back to when a Proposal Queue row's
// reverse link is clicked. Kept in App.tsx because the producer-page
// components don't know about each other.
const ORIGIN_TO_TAB: Record<OriginKey, Tab> = {
  // Autopilot is retired; historical proposals remain inspectable here.
  autopilot: 'proposal-queue',
  integrity: 'integrity-findings',
  document: 'document-ingest',
  emergent: 'emergent-queue',
  manual: 'proposal-queue',
};

// Nav-item key → origin it represents in the pending-proposal count.
// Proposal Queue itself aggregates all pending; the four producer tabs
// each show their own origin's pending count.
const TAB_TO_ORIGIN: Partial<Record<Tab, OriginKey | 'all'>> = {
  'integrity-findings': 'integrity',
  'document-ingest': 'document',
  'emergent-queue': 'emergent',
  'proposal-queue': 'all',
};

type Tab = 'home' | 'chat-history' | 'chat-graph' | 'explorer' | 'graph' | 'universe' | 'layer-heatmap' | 'query' | 'samples' | 'evaluation' | 'eval-runs' | 'refinements' | 'proposal-queue' | 'emergent-queue' | 'document-ingest' | 'integrity-dashboard' | 'integrity-scan' | 'integrity-findings' | 'synaptic-learning' | 'quality' | 'fairness' | 'performance' | 'knowledge-governance' | 'engrams' | 'agents' | 'query-landing' | 'knowledge-landing' | 'evaluate-landing' | 'history-landing' | 'mind-metrics' | 'mind-sessions' | 'mind-inbox' | 'mind-skills' | 'roadmap-ledgers' | 'carlos-lab' | 'nexus-lab' | 'oracle-funnel-lab' | 'architecture';

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
  /** Backend capability this page's API calls depend on. When the active
   *  profile does not grant it, the item is removed rather than shown and
   *  left to 404 (durability-tenant-composition, verification #6). */
  requires?: Capability;
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

const IconPulse = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 12h4l2.5-6 5 12 2.5-6h4" />
  </svg>
);

const IconLab = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.75 3h10.5A2 2 0 0 0 19 18l-5-9V3" />
    <path d="M7.5 15h9" />
  </svg>
);

function buildNavGroups(
  _tenantId: string | undefined,
  memorySurface = false,
  capabilities?: Capability[],
): NavGroup[] {
  const groups: NavGroup[] = [];

  if (!memorySurface) groups.push(
    {
      label: 'Query',
      landingKey: 'query-landing',
      description: 'Run and test queries against the neuron graph',
      icon: IconTerminal,
      items: [
        { key: 'query', label: 'Query Lab', description: 'Run queries against the neuron graph', requires: 'knowledge_graph' },
        { key: 'samples', label: 'Samples', description: 'Pre-built queries for testing and demos', requires: 'knowledge_graph' },
      ],
    },
  );

  groups.push(
    {
      label: 'Memory',
      landingKey: 'knowledge-landing',
      description: 'Inspect what Corvus knows and how it learned',
      icon: IconNetwork,
      items: [
        { key: 'explorer', label: 'Explorer', description: 'Browse and edit individual neurons', requires: 'knowledge_graph' },
        { key: 'engrams', label: 'Engrams', description: 'Source documents linked to the graph', requires: 'knowledge_graph' },
        { key: 'mind-skills', label: 'Skills', description: 'Compiled playbooks and their source health', requires: 'memory' },
        { key: 'synaptic-learning', label: 'Synaptic Learning', description: 'Learned patterns from query feedback' },
        { key: 'layer-heatmap', label: 'Layer Heatmap', description: 'Activity heatmap across graph layers', requires: 'knowledge_graph' },
      ],
    },
    {
      label: 'Steer',
      landingKey: 'proposal-queue',
      description: 'Resolve judgment and preserve forward intent',
      icon: IconCompass,
      items: [
        { key: 'mind-inbox', label: 'Inbox', description: 'Everything awaiting your judgment', requires: 'governance' },
        { key: 'proposal-queue', label: 'Proposal Queue', description: 'Review and approve proposed graph changes', requires: 'governance' },
        { key: 'roadmap-ledgers', label: 'Roadmap Ledgers', description: 'Project-scoped long-horizon plans, gates, and durable kickoff context', requires: 'operator' },
        { key: 'refinements', label: 'Refinements', description: 'History of neuron updates and changes', requires: 'governance' },
      ],
    },
    {
      label: 'Observe',
      landingKey: 'evaluate-landing',
      description: 'Read the system’s health, behavior, and evidence',
      icon: IconPulse,
      items: [
        { key: 'mind-metrics', label: 'Pallium', description: 'Pallium performance, trust, growth, and cost', requires: 'memory' },
        { key: 'mind-sessions', label: 'Sessions', description: "Episode logs: the memory's inputs and their distillation" },
        { key: 'performance', label: 'Performance', description: 'Volume, cost, scoring health, spread activation, and per-stage pipeline latency', requires: 'evaluation' },
        { key: 'knowledge-governance', label: 'Governance', description: 'Knowledge governance and compliance metrics', requires: 'compliance' },
        { key: 'quality', label: 'Quality', description: 'Response quality scoring and trends' },
        { key: 'fairness', label: 'Fairness', description: 'Bias detection across departments and roles' },
        { key: 'evaluation', label: 'Evaluation', description: 'Per-query evaluation scores and history', requires: 'evaluation' },
        { key: 'eval-runs', label: 'Eval Runs', description: 'Immutable eval artifacts — certify a run to stamp /v1/query', requires: 'evaluation' },
        { key: 'architecture', label: 'Architecture', description: "The system's own shape — boxes, drift, and the frontend/backend route join", requires: 'operator' },
      ],
    },
  );

  groups.push({
    label: 'Carlos Lab',
    landingKey: 'carlos-lab',
    description: 'Imported experimental lenses, isolated from production surfaces',
    icon: IconLab,
    items: [
      {
        key: 'nexus-lab',
        label: 'Nexus Graph',
        description: 'Projected-shell alternate view of the active graph',
        className: 'carlos-lab-nav-item',
        labelColor: '#ff4fd8',
      },
      {
        key: 'oracle-funnel-lab',
        label: 'Oracle Funnel',
        description: 'LoCoMo loss attribution from real eval artifacts',
        className: 'carlos-lab-nav-item',
        labelColor: '#45e6ff',
      },
    ],
  });

  // Composition filter first: never advertise a page whose backend routes this
  // process did not mount. An older backend omits the field entirely, in which
  // case nothing is filtered and the legacy rules below still apply.
  if (capabilities) {
    const granted = new Set<Capability>(capabilities);
    for (const g of groups) {
      g.items = g.items.filter(i => !i.requires || granted.has(i.requires));
    }
  }

  if (memorySurface) {
    // Editorial curation, NOT capability composition — the two were conflated
    // before 2026-07-31 and they answer different questions. The filter above
    // asks "did the backend mount this?" and is authoritative. This list asks
    // "is this page meaningful on a memory tenant?", and every key below is
    // backed by a capability corvus-mind still grants:
    //   emergent-queue, integrity-*  -> operator/governance, both also serve
    //                                   the Inbox and Proposal Queue
    //   document-ingest              -> ingestion, which also serves the
    //                                   harness capture path /ingest/observations
    //   engrams, layer-heatmap       -> knowledge_graph, which recall depends on
    //   eval-runs, evaluation        -> evaluation, which also serves Performance
    //   quality, fairness            -> no API client imports at all; inert pages
    // knowledge-governance stays curated out rather than composed out. It is
    // the one entry a capability could genuinely govern, but dropping
    // `compliance` from corvus-mind also removes /admin/system-banner and
    // /admin/audit-log*, which routers/compliance.py owns despite their being
    // operator surfaces. See that tenant.yaml comment; the split belongs to
    // roadmap record 04. Once compliance can be composed away cleanly, this
    // key moves to `requires: 'compliance'` above and leaves this list.
    //
    // Performance stays visible: recall queries carry full stage telemetry, so
    // per-step speed is real data on memory tenants too.
    const curatedOut = new Set(['emergent-queue', 'document-ingest',
      'engrams', 'layer-heatmap', 'knowledge-governance', 'quality',
      'fairness', 'evaluation', 'eval-runs',
      'integrity-dashboard', 'integrity-scan', 'integrity-findings']);
    for (const g of groups) g.items = g.items.filter(i => !curatedOut.has(i.key));
    return groups.filter(g => g.items.length > 0);
  }
  for (const g of groups) g.items = g.items.filter(i => i.key !== 'roadmap-ledgers');
  return groups.filter(g => g.items.length > 0);
}

function getInitialTheme(): Theme {
  const saved = localStorage.getItem('corvus-theme');
  if (saved && saved in THEME_LABELS) {
    return saved as Theme;
  }
  return 'corvus-native';
}

// ── Window manager ──
// Every page opens as a floating window over the ASCII-substrate desktop
// (one window per page; the nav panel is the only unclosable surface).
const WINDOWS_STORE_KEY = 'corvus-windows-v1';

function defaultWindows(): Record<string, WinState> {
  // Fresh state is a bare desktop: just the water and the collapsed nav
  // pill. Everything opens from the nav.
  return {};
}

function loadWindows(): Record<string, WinState> {
  try {
    const saved = JSON.parse(localStorage.getItem(WINDOWS_STORE_KEY) ?? '');
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
      const out: Record<string, WinState> = {};
      for (const [rawKey, v] of Object.entries(saved as Record<string, WinState>)) {
        if (typeof v?.x !== 'number' || typeof v?.w !== 'number') continue;
        // Migration: the tabbed Integrity page was split into three windows.
        const key = rawKey === 'integrity' ? 'integrity-dashboard' : rawKey;
        // Clamp restored rects so a window can never come back off-screen.
        const w = Math.max(MIN_W, Math.min(v.w, window.innerWidth - 16));
        const h = Math.max(MIN_H, Math.min(v.h, window.innerHeight - 16));
        const x = Math.max(8 - w + 100, Math.min(v.x, window.innerWidth - 100));
        const y = Math.max(0, Math.min(v.y, window.innerHeight - 60));
        out[key] = { ...v, x, y, w, h };
      }
      if (Object.keys(out).length > 0) return out;
    }
  } catch { /* fall through to default */ }
  return defaultWindows();
}

export default function App() {
  const [windows, setWindows] = useState<Record<string, WinState>>(loadWindows);
  const [explorerNeuronId, setExplorerNeuronId] = useState<number | null>(null);
  const [proposedByOrigin, setProposedByOrigin] = useState<Record<string, number>>({});
  const [totalProposed, setTotalProposed] = useState(0);
  const [queueInitialOrigin, setQueueInitialOrigin] = useState<OriginFilter | undefined>(undefined);
  // Floating nav: collapsed = logo-only pill; position is draggable and
  // persisted. Defaults just off the top-left corner, collapsed (a fresh
  // page opens as a bare desktop with only the pulsing logo).
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('corvus-nav-collapsed') !== '0');
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
  const [navFlyout, setNavFlyout] = useState<string | null>(null);
  const isMobile = useIsMobile();
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const [desktopGraphVisible, setDesktopGraphVisible] = useState(() => localStorage.getItem('corvus-desktop-graph-visible') !== '0');
  const [navHeight, setNavHeight] = useState(52);
  const [navWidth, setNavWidth] = useState(148);
  const [graphControlsHeight, setGraphControlsHeight] = useState(0);
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

  // Memory tenants land on the Memory dashboard, not the chat hero.
  const landedRef = useRef(false);
  useEffect(() => {
    if (tenantConfig?.memory_surface && !landedRef.current) {
      landedRef.current = true;
      openWindow('mind-metrics');
    }
  }, [tenantConfig?.memory_surface]); // eslint-disable-line react-hooks/exhaustive-deps

  const displayName = tenantConfig?.display_name ?? 'Corvus';

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('corvus-theme', theme);
  }, [theme]);
  useEffect(() => { localStorage.setItem('corvus-desktop-graph-visible', desktopGraphVisible ? '1' : '0'); }, [desktopGraphVisible]);

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

  // ── Window manager callbacks ──
  const focusWindow = useCallback((key: string) => {
    setWindows(prev => {
      const win = prev[key];
      if (!win) return prev;
      const top = Object.values(prev).reduce((m, v) => Math.max(m, v.z), 9);
      if (win.z === top && !win.min) return prev;
      let next: Record<string, WinState> = { ...prev, [key]: { ...win, z: top + 1, min: false } };
      // z grows monotonically with every focus; renormalize before it
      // collides with the nav layer (z 900).
      if (top + 1 > 500) {
        const sorted = Object.entries(next).sort((a, b) => a[1].z - b[1].z);
        next = {};
        sorted.forEach(([k, v], i) => { next[k] = { ...v, z: 10 + i }; });
      }
      return next;
    });
  }, []);

  // Accepts any window key: static Tab keys plus dynamic ones ("agent:<name>").
  // `rect` sets a preferred initial placement for windows that aren't open yet.
  const openWindow = useCallback((key: string, rect?: WinRect) => {
    setWindows(prev => {
      const top = Object.values(prev).reduce((m, v) => Math.max(m, v.z), 9);
      const existing = prev[key];
      if (existing) return { ...prev, [key]: { ...existing, min: false, z: top + 1 } };
      // Cascade new windows down-right from just beside the nav's default spot.
      const off = (Object.keys(prev).length % 7) * 26;
      const fallback: WinRect = {
        x: 280 + off,
        y: 56 + off,
        w: Math.min(1000, window.innerWidth - 340),
        h: Math.min(660, window.innerHeight - 120),
      };
      return { ...prev, [key]: { ...(rect ?? fallback), z: top + 1, min: false, max: false } };
    });
  }, []);
  // Historical name — every pre-windowing call site "switches tab" by
  // opening (or focusing) that page's window.
  const setTab = openWindow;

  const closeWindow = useCallback((key: string) => {
    setWindows(prev => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const minimizeWindow = useCallback((key: string) => {
    setWindows(prev => prev[key] ? { ...prev, [key]: { ...prev[key], min: true } } : prev);
  }, []);

  const toggleMaxWindow = useCallback((key: string) => {
    setWindows(prev => {
      const win = prev[key];
      if (!win) return prev;
      if (win.max) {
        const r = win.restore ?? { x: 80, y: 60, w: 900, h: 600 };
        return { ...prev, [key]: { ...win, ...r, max: false } };
      }
      return { ...prev, [key]: { ...win, max: true, restore: { x: win.x, y: win.y, w: win.w, h: win.h } } };
    });
  }, []);

  const commitWindow = useCallback((key: string, patch: Partial<WinState>) => {
    setWindows(prev => prev[key] ? { ...prev, [key]: { ...prev[key], ...patch } } : prev);
  }, []);

  // Persist the layout and let the wake re-mask around the new rects.
  useEffect(() => {
    localStorage.setItem(WINDOWS_STORE_KEY, JSON.stringify(windows));
    window.dispatchEvent(new Event('corvus-wake-refresh'));
  }, [windows]);

  // When a chat starts in the Home window, open its two companion windows
  // (history left, neuron graph right) and keep the chat focused.
  useEffect(() => {
    const onChatStarted = () => {
      const vw = window.innerWidth, vh = window.innerHeight;
      openWindow('chat-history', { x: 16, y: Math.max(72, vh - 480), w: 300, h: Math.min(440, vh - 96) });
      openWindow('chat-graph', { x: Math.max(320, vw - 436), y: 56, w: 420, h: Math.min(620, vh - 120) });
      focusWindow('home');
    };
    window.addEventListener(CHAT_STARTED_EVENT, onChatStarted);
    return () => window.removeEventListener(CHAT_STARTED_EVENT, onChatStarted);
  }, [openWindow, focusWindow]);

  // Walkthrough "open this section" actions (see DemoHelper.tsx).
  useEffect(() => {
    const onOpen = (e: Event) => openWindow((e as CustomEvent).detail as string);
    window.addEventListener(OPEN_WINDOW_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_WINDOW_EVENT, onOpen);
  }, [openWindow]);

  // History-window actions must work even when the chat window is closed:
  // "New Chat" opens a fresh hero, and a session click opens Home, whose
  // mount consumes the pending session id (see chatBus.ts).
  useEffect(() => {
    const onChatIntent = () => openWindow('home');
    window.addEventListener(CHAT_NEW_EVENT, onChatIntent);
    window.addEventListener(CHAT_LOAD_SESSION_EVENT, onChatIntent);
    return () => {
      window.removeEventListener(CHAT_NEW_EVENT, onChatIntent);
      window.removeEventListener(CHAT_LOAD_SESSION_EVENT, onChatIntent);
    };
  }, [openWindow]);

  // Topmost non-minimized window drives nav highlighting.
  const focusedKey = useMemo(() => {
    let k: string | null = null, top = -1;
    for (const [key, w] of Object.entries(windows)) {
      if (!w.min && w.z > top) { top = w.z; k = key; }
    }
    return k;
  }, [windows]);

  // Forward deep-link: producer nav badge → Proposal Queue filtered to origin.
  const navigateToFilteredQueue = useCallback((origin: OriginKey | 'all') => {
    setQueueInitialOrigin(origin === 'all' ? undefined : (origin as OriginFilter));
    setTab('proposal-queue');
  }, []);

  function setTheme(t: Theme) {
    setThemeState(t);
    setThemeMenuOpen(false);
  }

  // Keep the floating nav fully on-screen (8px margin all around).
  const clampNavPos = useCallback((p: { x: number; y: number }) => {
    const el = navRef.current;
    const w = el?.offsetWidth ?? 220;
    const h = el?.offsetHeight ?? 52;
    const x = Math.max(8, Math.min(p.x, window.innerWidth - w - 8));
    const clusterHeight = desktopGraphVisible ? h + 8 + graphControlsHeight : h;
    const y = Math.max(8, Math.min(p.y, window.innerHeight - clusterHeight - 8));
    return x === p.x && y === p.y ? p : { x, y };
  }, [desktopGraphVisible, graphControlsHeight]);

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
    if (!navRef.current) return;
    const report = () => {
      setNavHeight(navRef.current?.offsetHeight || 52);
      setNavWidth(navRef.current?.offsetWidth || 148);
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(navRef.current);
    return () => observer.disconnect();
  }, [authStatus, isMobile]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setNavHeight(navRef.current?.offsetHeight || 52);
      setNavWidth(navRef.current?.offsetWidth || 148);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [collapsed, authStatus]);

  useEffect(() => { setNavPos(p => clampNavPos(p)); }, [navHeight, graphControlsHeight, desktopGraphVisible, clampNavPos]);

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

  // Flyouts are contextual and transient. Escape or a click outside returns
  // the Perch to its compact rail without changing any open windows.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (navRef.current && !navRef.current.contains(event.target as Node)) setNavFlyout(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNavFlyout(null);
    };
    document.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  // Build nav groups based on tenant (memoized — page-element identity
  // depends on it, so it must be referentially stable between renders)
  const navGroups = useMemo(
    () => buildNavGroups(tenantConfig?.tenant_id, !!tenantConfig?.memory_surface, tenantConfig?.capabilities),
    [tenantConfig?.tenant_id, tenantConfig?.memory_surface, tenantConfig?.capabilities],
  );
  const activeGroup = navGroups.find(g => g.landingKey === focusedKey || g.items.some(i => i.key === focusedKey))?.label;

  const windowTitle = useCallback((key: string): string => {
    if (key === 'home') return displayName;
    if (key === 'chat-history') return 'Chat History';
    if (key === 'chat-graph') return 'Neuron Graph';
    if (key.startsWith('agent:')) return friendlyName(key.slice(6));
    for (const g of navGroups) {
      const item = g.items.find(i => i.key === key);
      if (item) return item.label;
      if (g.landingKey === key) return g.label;
    }
    return key;
  }, [displayName, navGroups]);

  // Page content factory. The opener is INJECTED so the same pages work in
  // both shells: desktop passes openWindow, the mobile shell passes its
  // openView — in-page navigation (Home links, landing pages, proposal
  // deep-links) then targets whichever shell is actually rendered. Memoized
  // so drag/resize commits and the 30s proposal poll re-render App without
  // re-rendering every mounted page (element identity unchanged → React
  // bails out of those subtrees).
  const makeRenderPage = useCallback((open: (key: string) => void) => {
    const goToNeuron = (id: number) => { setExplorerNeuronId(id); open('explorer'); };
    // Reverse deep-link: Proposal Queue row → producer page. (Future: stash
    // target ids in per-page focus state so the producer highlights the row.)
    const goToProducer = (target: ProposalProducerTarget) => {
      open(ORIGIN_TO_TAB[target.origin as OriginKey] ?? 'proposal-queue');
    };
    return (key: string): ReactNode => {
    if (key.startsWith('agent:')) return <SingleAgentPane name={key.slice(6)} />;
    switch (key as Tab) {
      case 'home': return <HomePage onNavigate={k => open(k)} />;
      case 'chat-history': return <ChatHistoryWindow />;
      case 'chat-graph': return <NeuronGraphWindow />;
      case 'explorer': return <Explorer navigateToNeuronId={explorerNeuronId} onNavigateHandled={() => setExplorerNeuronId(null)} />;
      case 'engrams': return <EngramPage />;
      case 'agents': return <AgentsPage onOpenAgent={name => open(`agent:${name}`)} />;
      case 'graph': return <CirclePacking />;
      case 'universe': return <NeuronUniverse />;
      case 'layer-heatmap': return <LayerHeatmap />;
      case 'query': return <QueryLab onNavigateToNeuron={goToNeuron} />;
      case 'evaluation': return <EvaluationPage />;
      case 'eval-runs': return <EvalRunsPage />;
      case 'refinements': return <RefinementHistory />;
      case 'samples': return <SampleQueries />;
      case 'proposal-queue': return <ProposalQueuePage initialOriginFilter={queueInitialOrigin} onNavigateToProducer={goToProducer} />;
      case 'emergent-queue': return <EmergentQueuePage />;
      case 'document-ingest': return <DocumentIngestPage />;
      case 'integrity-dashboard': return <IntegrityPage panel="dashboard" />;
      case 'integrity-scan': return <IntegrityPage panel="scan" />;
      case 'integrity-findings': return <IntegrityPage panel="findings" />;
      case 'synaptic-learning': return <SynapticLearningPage />;
      case 'quality': return <QualityPage />;
      case 'fairness': return <FairnessPage />;
      case 'performance': return <PerformancePage />;
      case 'mind-metrics': return <MindMetricsPage />;
      case 'architecture': return <ArchitecturePage />;
      case 'mind-sessions': return <MindSessionsPage />;
      case 'mind-inbox': return <MindInboxPage />;
      case 'mind-skills': return <MindSkillsPage />;
      case 'roadmap-ledgers': return <RoadmapLedgersPage />;
      case 'nexus-lab': return <NexusLabPage />;
      case 'oracle-funnel-lab': return <OracleFunnelLabPage />;
      case 'knowledge-governance': return <KnowledgeGovernancePage />;
      default: {
        const group = navGroups.find(g => g.landingKey === key);
        if (!group) return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Unknown page: {key}</div>;
        return (
          <GroupLandingPage
            title={group.label}
            icon={group.icon}
            description={group.description}
            items={group.items.map(i => ({ key: i.key, label: i.label, description: i.description }))}
            onNavigate={k => open(k)}
          />
        );
      }
    }
    };
  }, [explorerNeuronId, queueInitialOrigin, navGroups]);

  const renderPage = useMemo(() => makeRenderPage(openWindow), [makeRenderPage, openWindow]);

  const openKeysSig = Object.keys(windows).join('|');
  const pageElements = useMemo(() => {
    const m: Record<string, ReactNode> = {};
    for (const key of openKeysSig ? openKeysSig.split('|') : []) m[key] = renderPage(key);
    return m;
  }, [openKeysSig, renderPage]);

  // Auth gate
  if (authStatus === 'checking') {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--text-dim)' }}>Loading...</div>;
  }

  if (authStatus === 'needs_key') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg)' }}>
        <div style={{ textAlign: 'center', maxWidth: 360 }}>
          <img src={corvusLogo} alt="Corvus" style={{ width: 64, height: 64, marginBottom: 16, opacity: 0.8 }} />
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

  // Adaptive fork (roadmap prod-mobile-shell): coarse-pointer / narrow
  // viewports get the mobile shell — full-screen views, bottom bar, view
  // switcher — instead of the cursor-driven windowing desktop.
  if (isMobile) {
    return (
      <MobileShell
        displayName={displayName}
        navGroups={navGroups}
        windowTitle={windowTitle}
        makeRenderPage={makeRenderPage}
        demoKeyWizard={DemoKeyWizard ? <Suspense fallback={null}><DemoKeyWizard /></Suspense> : null}
      />
    );
  }

  return (
    <div className="app app-desktop">
      <SystemUseBanner />
      {/* Desktop: always-present ASCII substrate + faint brand mark.
          Windows float above it; the wake breaks around every
          [data-wake-obstacle] (windows, nav, dock). */}
      <div className="desktop-layer">
        <AsciiWake />
        <div className="desktop-brand">
          <img src={corvusLogo} alt="" draggable={false} />
          <span>{displayName}</span>
        </div>
      </div>
      {desktopGraphVisible && <div className="desktop-neuron-layer" data-testid="desktop-neuron-layer">
        <NeuronUniverse transparent
          controlPosition={{ left: navPos.x, top: navPos.y + navHeight + 8, width: navWidth }}
          onControlPointerDown={startNavDrag}
          controlDragMoved={() => navDragMovedRef.current}
          onControlHeightChange={setGraphControlsHeight}
        />
      </div>}
      <aside
        ref={navRef}
        className={`sidebar${collapsed ? ' sidebar-pill' : ''}`}
        style={{ left: navPos.x, top: navPos.y }}
        data-wake-obstacle
        data-wake-pad={collapsed ? '0' : undefined}
        data-wake-pulse={collapsed ? true : undefined}
        onMouseLeave={() => setNavFlyout(null)}
      >
        {collapsed ? (
          /* Logo pill: drag to move, click to expand. Collapsed, the card
             itself is the wake obstacle with zero padding — the water
             breaks exactly at the card's border — and it emits a gentle
             periodic ripple (data-wake-pulse). */
          <button
            className="sidebar-pill-btn"
            onPointerDown={startNavDrag}
            onClick={() => { if (!navDragMovedRef.current) setCollapsed(false); }}
            title="Open navigation (drag to move)"
          >
            <img src={corvusLogo128} alt="Corvus" className="sidebar-logo" draggable={false} />
          </button>
        ) : (
          <>
            {/* The logo is the Perch's physical handle: drag the rail from
                here, or click it back down to the original breathing pebble. */}
            <button
              className="perch-handle"
              onPointerDown={startNavDrag}
              onClick={() => {
                if (!navDragMovedRef.current) {
                  setNavFlyout(null);
                  setCollapsed(true);
                }
              }}
              title={`${displayName} · click to collapse · drag to move`}
              aria-label={`Collapse ${displayName} navigation; drag to move`}
            >
              <img src={corvusLogo128} alt="" className="sidebar-logo" draggable={false} />
              <span aria-hidden="true">CORVUS</span>
            </button>
            <nav className="perch-nav" aria-label="Primary navigation">
              {/* Chat remains a first-class direct action on knowledge
                  tenants. Memory tenants work through their coding harness,
                  so the rail begins with the memory itself. */}
            {!tenantConfig?.memory_surface && (
              <div className="perch-mode">
                <button
                  className={`perch-mode-btn${focusedKey === 'home' ? ' active' : ''}${windows.home ? ' open' : ''}`}
                  onClick={() => { setQueueInitialOrigin(undefined); setTab('home'); }}
                  title="Chat"
                >
                  <span className="perch-mode-icon" aria-hidden="true">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
                    </svg>
                  </span>
                  <span className="perch-mode-label">Chat</span>
                  {windows.home && <span className="perch-open-dot" aria-label="Open" />}
                </button>
              </div>
            )}
            {navGroups.map(group => (
              <div
                key={group.label}
                className="perch-mode"
                onMouseEnter={() => setNavFlyout(group.label)}
              >
                <button
                  className={`perch-mode-btn${activeGroup === group.label ? ' active' : ''}${group.items.some(item => windows[item.key]) ? ' open' : ''}`}
                  onClick={() => setNavFlyout(group.label)}
                  aria-expanded={navFlyout === group.label}
                  aria-controls={`perch-${group.label.toLowerCase()}-flyout`}
                  title={group.label}
                >
                  <span className="perch-mode-icon" aria-hidden="true">{group.icon}</span>
                  <span className="perch-mode-label">{group.label}</span>
                  {group.items.some(item => windows[item.key]) && <span className="perch-open-dot" aria-label="Contains open windows" />}
                  {group.items.some(item => item.key === 'proposal-queue') && totalProposed > 0 && (
                    <span className="perch-mode-badge" aria-label={`${totalProposed} pending proposals`}>{totalProposed}</span>
                  )}
                </button>
                {navFlyout === group.label && (
                  <section
                    id={`perch-${group.label.toLowerCase()}-flyout`}
                    className="perch-flyout"
                    aria-label={`${group.label} pages`}
                    data-wake-obstacle
                  >
                    <header className="perch-flyout-header">
                      <span className="perch-flyout-icon" aria-hidden="true">{group.icon}</span>
                      <span>
                        <strong>{group.label}</strong>
                        <small>{group.description}</small>
                      </span>
                    </header>
                    <div className="perch-flyout-items">
                    {group.items.map(item => {
                      const originForTab = TAB_TO_ORIGIN[item.key];
                      const count = originForTab === 'all'
                        ? totalProposed
                        : (originForTab ? (proposedByOrigin[originForTab] ?? 0) : 0);
                      return (
                        <button
                          key={item.key}
                          className={`perch-flyout-item${focusedKey === item.key ? ' active' : ''}${windows[item.key] ? ' open' : ''}${item.className ? ' ' + item.className : ''}`}
                          onClick={() => {
                            // Click body of nav item goes to the page itself. Badge
                            // has its own click handler (see below) that deep-links
                            // to the filtered Proposal Queue.
                            setQueueInitialOrigin(undefined);
                            setTab(item.key);
                            setNavFlyout(null);
                          }}
                        >
                          <span className="perch-item-state" aria-hidden="true" />
                          <span className="perch-item-copy">
                            <strong style={{ color: item.labelColor }}>{item.label}</strong>
                            <small>{item.description}</small>
                          </span>
                          {count > 0 && item.key !== 'proposal-queue' && originForTab && originForTab !== 'all' && (
                            <span
                              className="perch-item-badge"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                navigateToFilteredQueue(originForTab);
                              }}
                              title={`${count} pending in Proposal Queue — click to filter`}
                            >
                              {count}
                            </span>
                          )}
                          {count > 0 && item.key === 'proposal-queue' && (
                            <span
                              className="perch-item-badge"
                              title={`${count} pending proposals`}
                            >
                              {count}
                            </span>
                          )}
                        </button>
                      );
                    })}
                    </div>
                  </section>
                )}
              </div>
            ))}
            </nav>
        {/* Utilities are deliberately subordinate to the four product jobs. */}
        <div className="sidebar-settings-area">
          <button
            className={`sidebar-settings-btn sidebar-graph-btn${desktopGraphVisible ? ' active' : ''}`}
            onClick={() => setDesktopGraphVisible(v => !v)}
            title={desktopGraphVisible ? 'Hide neuron graph' : 'Show neuron graph'}
            aria-label={desktopGraphVisible ? 'Hide neuron graph' : 'Show neuron graph'}
            aria-pressed={desktopGraphVisible}
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <circle cx="5" cy="12" r="2.2" /><circle cx="12" cy="5" r="2.2" /><circle cx="19" cy="10" r="2.2" /><circle cx="14" cy="19" r="2.2" />
              <path d="M6.6 10.4l3.8-3.8M14 5.8l3.2 2.8M17.6 11.8l-2.3 5M6.9 13.1l5.2 4.5M7.1 12l9.7-1.6" />
            </svg>
          </button>
          <button
            className="sidebar-settings-btn sidebar-walkthrough-btn"
            onClick={() => window.dispatchEvent(new Event(START_TOUR_EVENT))}
            title="Walkthrough"
            aria-label="Start walkthrough"
          >
            <span aria-hidden="true">?</span>
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
                left: Math.max(8, Math.min(r.left, window.innerWidth - 272)),
                top: Math.max(8, Math.min(r.bottom + 8, window.innerHeight - 620)),
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
            <div className="settings-popup-section">
              <label className="settings-label">ASCII Wake</label>
              <WakeSettingsPanel />
            </div>
          </div>
        </>
      )}
      {/* Floating windows — one per open page, kept mounted while minimized */}
      {Object.keys(windows).map(key => (
        <AppWindow
          key={key}
          title={windowTitle(key)}
          state={windows[key]}
          focused={focusedKey === key}
          onFocus={() => focusWindow(key)}
          onClose={() => closeWindow(key)}
          onMinimize={() => minimizeWindow(key)}
          onToggleMax={() => toggleMaxWindow(key)}
          onCommit={patch => commitWindow(key, patch)}
        >
          {pageElements[key]}
        </AppWindow>
      ))}

      {DemoKeyWizard && <Suspense fallback={null}><DemoKeyWizard /></Suspense>}
      <DemoHelper />

      {/* Dock of minimized windows */}
      {Object.values(windows).some(w => w.min) && (
        <div className="window-dock" data-wake-obstacle>
          {Object.keys(windows).filter(k => windows[k].min).map(k => (
            <button
              key={k}
              className="window-dock-pill"
              onClick={() => focusWindow(k)}
              title={`Restore ${windowTitle(k)}`}
            >
              {windowTitle(k)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
