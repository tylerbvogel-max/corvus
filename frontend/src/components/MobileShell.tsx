import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react';
import corvusLogo from '../assets/corvus-logo.png';
import corvusLogo128 from '../assets/corvus-logo-128.png';
import AsciiWake from './AsciiWake';
import SystemUseBanner from './SystemUseBanner';
import { OPEN_WINDOW_EVENT } from './DemoHelper';
import { CHAT_STARTED_EVENT, CHAT_NEW_EVENT, CHAT_LOAD_SESSION_EVENT } from '../chatBus';

/* Adaptive mobile shell (roadmap prod-mobile-shell).

   The desktop windowing metaphor — draggable panes, hover interactions,
   cursor-driven wake — does not translate to touch, so instead of
   responsive CSS this is a FORK: on coarse-pointer / narrow viewports the
   app renders this shell, where every desktop window becomes a full-screen
   view, the nav panel becomes a Menu sheet off a bottom bar, and the dock
   becomes the view switcher. The state model maps one-to-one: open views =
   open windows, the active view = the focused window. Open views stay
   MOUNTED while inactive (display:none) so a chat in progress survives
   switching views, exactly like minimized desktop windows.

   The water stays as the background brand: AsciiWake already splashes on
   pointerdown, and taps are pointerdowns — continuous cursor wake simply
   never fires on touch. */

export function useIsMobile(): boolean {
  // Phone-width always; tablet-width only when the pointer is coarse
  // (a narrow desktop browser window still gets the desktop UI).
  const query = '(max-width: 767px), (pointer: coarse) and (max-width: 1023px)';
  const [mobile, setMobile] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMobile(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return mobile;
}

export interface MobileNavItem {
  key: string;
  label: string;
  description: string;
}

export interface MobileNavGroup {
  label: string;
  landingKey: string;
  description: string;
  icon: ReactNode;
  items: MobileNavItem[];
}

interface MobileShellProps {
  displayName: string;
  navGroups: MobileNavGroup[];
  windowTitle: (key: string) => string;
  // Factory: given the shell's open-view function, returns the page renderer.
  // Injecting `open` here is what makes in-page navigation (Home links,
  // landing pages, proposal deep-links) target mobile views instead of the
  // invisible desktop window state.
  makeRenderPage: (open: (key: string) => void) => (key: string) => ReactNode;
  demoKeyWizard?: ReactNode;
}

const VIEWS_KEY = 'corvus-mobile-views-v1';
const ACTIVE_KEY = 'corvus-mobile-active-v1';

function loadViews(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEWS_KEY) ?? '');
    if (Array.isArray(saved) && saved.every(v => typeof v === 'string')) return saved;
  } catch { /* fall through */ }
  return ['home'];
}

export default function MobileShell({
  displayName, navGroups, windowTitle, makeRenderPage, demoKeyWizard,
}: MobileShellProps) {
  const [views, setViews] = useState<string[]>(loadViews);
  const [active, setActive] = useState<string | null>(
    () => localStorage.getItem(ACTIVE_KEY) || 'home',
  );
  const [sheet, setSheet] = useState<'none' | 'nav' | 'views'>('none');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());

  useEffect(() => { localStorage.setItem(VIEWS_KEY, JSON.stringify(views)); }, [views]);
  useEffect(() => {
    if (active) localStorage.setItem(ACTIVE_KEY, active);
    else localStorage.removeItem(ACTIVE_KEY);
  }, [active]);

  const openView = useCallback((key: string) => {
    setViews(prev => (prev.includes(key) ? prev : [...prev, key]));
    setActive(key);
    setSheet('none');
  }, []);

  // Open without stealing focus — the mobile twin of the desktop behavior
  // where a chat's companion windows open behind the focused chat.
  const openViewInBackground = useCallback((key: string) => {
    setViews(prev => (prev.includes(key) ? prev : [...prev, key]));
  }, []);

  const closeView = useCallback((key: string) => {
    setViews(prev => {
      const next = prev.filter(v => v !== key);
      setActive(a => (a === key ? (next[next.length - 1] ?? null) : a));
      return next;
    });
  }, []);

  // Same app-wide navigation events the desktop listens for.
  useEffect(() => {
    const onChatStarted = () => {
      openViewInBackground('chat-history');
      openViewInBackground('chat-graph');
    };
    const onChatIntent = () => openView('home');
    const onOpen = (e: Event) => openView((e as CustomEvent).detail as string);
    window.addEventListener(CHAT_STARTED_EVENT, onChatStarted);
    window.addEventListener(CHAT_NEW_EVENT, onChatIntent);
    window.addEventListener(CHAT_LOAD_SESSION_EVENT, onChatIntent);
    window.addEventListener(OPEN_WINDOW_EVENT, onOpen);
    return () => {
      window.removeEventListener(CHAT_STARTED_EVENT, onChatStarted);
      window.removeEventListener(CHAT_NEW_EVENT, onChatIntent);
      window.removeEventListener(CHAT_LOAD_SESSION_EVENT, onChatIntent);
      window.removeEventListener(OPEN_WINDOW_EVENT, onOpen);
    };
  }, [openView, openViewInBackground]);

  const renderPage = useMemo(() => makeRenderPage(openView), [makeRenderPage, openView]);

  // Views stay mounted while open (chat state survives switching); only the
  // active one is displayed.
  const pageElements = useMemo(() => {
    const m: Record<string, ReactNode> = {};
    for (const key of views) m[key] = renderPage(key);
    return m;
  }, [views, renderPage]);

  const toggleGroup = (label: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label); else next.add(label);
      return next;
    });
  };

  return (
    <div className="mobile-shell">
      <SystemUseBanner />
      {/* Water substrate — taps splash (AsciiWake pointerdown handler). */}
      <div className="desktop-layer">
        <AsciiWake />
        <div className="desktop-brand">
          <img src={corvusLogo} alt="" draggable={false} />
          <span>{displayName}</span>
        </div>
      </div>

      {views.map(key => (
        <section
          key={key}
          className={`mobile-view${key === active ? ' mobile-view-active' : ''}`}
          data-wake-obstacle={key === active ? true : undefined}
        >
          <header className="mobile-view-header">
            <span className="mobile-view-title">{windowTitle(key)}</span>
            <button
              className="mobile-view-close"
              onClick={() => closeView(key)}
              aria-label={`Close ${windowTitle(key)}`}
            >&times;</button>
          </header>
          <div className="mobile-view-body">{pageElements[key]}</div>
        </section>
      ))}

      {active === null && (
        <div className="mobile-empty">
          <img src={corvusLogo128} alt="" draggable={false} />
          <p>Tap Menu to open a page,<br />or tap the water.</p>
        </div>
      )}

      <nav className="mobile-bottombar" data-wake-obstacle>
        <button
          className={`mobile-bottombar-btn${sheet === 'nav' ? ' active' : ''}`}
          onClick={() => setSheet(s => (s === 'nav' ? 'none' : 'nav'))}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
          </svg>
          <span>Menu</span>
        </button>
        <button
          className={`mobile-bottombar-btn mobile-bottombar-chat${active === 'home' ? ' active' : ''}`}
          onClick={() => openView('home')}
        >
          <img src={corvusLogo128} alt="" draggable={false} />
          <span>Chat</span>
        </button>
        <button
          className={`mobile-bottombar-btn${sheet === 'views' ? ' active' : ''}`}
          onClick={() => setSheet(s => (s === 'views' ? 'none' : 'views'))}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" />
          </svg>
          <span>Views{views.length > 0 ? ` (${views.length})` : ''}</span>
        </button>
      </nav>

      {sheet !== 'none' && (
        <div className="mobile-sheet-backdrop" onClick={() => setSheet('none')}>
          <div className="mobile-sheet" onClick={e => e.stopPropagation()}>
            {sheet === 'nav' ? (
              <>
                <div className="mobile-sheet-title">{displayName}</div>
                <button className="mobile-sheet-item" onClick={() => openView('home')}>Chat</button>
                <button className="mobile-sheet-item" onClick={() => openView('chat-history')}>Chat History</button>
                <button className="mobile-sheet-item" onClick={() => openView('chat-graph')}>Neuron Graph</button>
                {navGroups.map(group => (
                  <div key={group.label}>
                    <button className="mobile-sheet-group" onClick={() => toggleGroup(group.label)}>
                      <span className="mobile-sheet-chevron">{expandedGroups.has(group.label) ? '▾' : '▸'}</span>
                      <span className="mobile-sheet-group-icon">{group.icon}</span>
                      <span>{group.label}</span>
                    </button>
                    {expandedGroups.has(group.label) && group.items.map(item => (
                      <button
                        key={item.key}
                        className="mobile-sheet-item mobile-sheet-subitem"
                        onClick={() => openView(item.key)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ))}
              </>
            ) : (
              <>
                <div className="mobile-sheet-title">Open views</div>
                {views.length === 0 && (
                  <div className="mobile-sheet-empty">Nothing open — use Menu.</div>
                )}
                {views.map(key => (
                  <div key={key} className={`mobile-sheet-view-row${key === active ? ' active' : ''}`}>
                    <button className="mobile-sheet-item" onClick={() => openView(key)}>
                      {windowTitle(key)}
                    </button>
                    <button
                      className="mobile-sheet-view-close"
                      onClick={() => closeView(key)}
                      aria-label={`Close ${windowTitle(key)}`}
                    >&times;</button>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {demoKeyWizard}
    </div>
  );
}
