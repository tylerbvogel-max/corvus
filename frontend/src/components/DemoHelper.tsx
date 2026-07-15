import { useCallback, useEffect, useState } from 'react';

/* Demo helper — a floating "?" pill (deliberate sibling of the nav
   logo pill: same card form, but dashed accent border so it reads as
   the guide, not the app) that runs a spotlight walkthrough of every
   frontend surface. Present in all builds. Draggable like the nav
   pill; position persists. Steps target live elements when they exist
   and fall back to a hint ("open it via …") when they don't; optional
   steps (dock, demo-only pill) are skipped silently when absent. */

/** App.tsx listens for this and opens (or focuses) the window in detail. */
export const OPEN_WINDOW_EVENT = 'corvus-open-window';
export const START_TOUR_EVENT = 'corvus-start-tour';

interface TourStep {
  title: string;
  body: string;
  find?: () => HTMLElement | null;
  hint?: string;       // shown when find() comes up empty (and there's no open action)
  optional?: boolean;  // skip silently when missing
  /** One-click way to bring this section up when its target is closed. */
  open?: { label: string; run: () => void };
}

const q = (sel: string) => () => document.querySelector(sel) as HTMLElement | null;
const windowByTitle = (title: string) => () => {
  const el = Array.from(document.querySelectorAll('.app-window-title'))
    .find(t => t.textContent?.trim() === title);
  return (el?.closest('.app-window') as HTMLElement) ?? null;
};
const openWin = (key: string) => () =>
  window.dispatchEvent(new CustomEvent(OPEN_WINDOW_EVENT, { detail: key }));
const expandNav = () =>
  (document.querySelector('.sidebar-pill-btn') as HTMLElement | null)?.click();

const STEPS: TourStep[] = [
  {
    title: 'Welcome to the Corvus desktop',
    body: 'Everything floats on a live water simulation — move your mouse across the dark areas to carve a wake, click for a splash. Pages open as windows you can arrange however you like, and your layout is remembered.',
  },
  {
    title: 'The heartbeat logo is your navigation',
    body: 'Click it to expand the menu; click the title bar to collapse it back to this pill. Drag it anywhere — it stays where you put it. The water breaks against its edges, and it beats once a second.',
    find: q('.sidebar'),
  },
  {
    title: 'The navigation menu',
    body: 'CHAT opens the main conversation. Knowledge, Agency Lab, and Evaluate expand into their pages — each one opens as its own window. Numbers on items are pending proposals awaiting review.',
    find: q('.sidebar-nav'),
    open: { label: 'Expand the navigation', run: expandNav },
  },
  {
    title: 'Settings gear',
    body: 'Themes live here — and so does live tuning for the water itself (cell size, damping, wake strength, ambient drops…). Slide things around and watch the background react instantly.',
    find: q('.sidebar-settings-btn'),
    open: { label: 'Expand the navigation', run: expandNav },
  },
  {
    title: 'The chat',
    body: 'Ask anything — answers are grounded in internal documentation, with numbered citations you can click and a sources chip under each response. Starting a chat also opens the Chat History and Neuron Graph windows.',
    find: () => (document.querySelector('.chat-hero, .chat-main')?.closest('.app-window') as HTMLElement) ?? null,
    open: { label: 'Open the chat', run: openWin('home') },
  },
  {
    title: 'Model picker',
    body: 'Chooses which LLM answers. The roster reflects what this system actually has available right now.',
    find: q('.chat-model-select'),
    open: { label: 'Open the chat', run: openWin('home') },
  },
  {
    title: 'Seed prompts',
    body: 'One-click sample questions, good first taps to see grounded answers and the neuron graph light up.',
    find: q('.chat-seed-prompts'),
    optional: true,
  },
  {
    title: 'Windows work like a desktop',
    body: 'Drag by the title bar. Drag to a screen edge to snap half-screen, to a corner for quarters, to the top to maximize (double-clicking the title bar does that too). ─ minimizes to the dock, ✕ closes. Click any window to bring it to front.',
    find: q('.app-window-titlebar'),
    open: { label: 'Open a window', run: openWin('home') },
  },
  {
    title: 'Resize from any edge',
    body: 'Every edge and corner drags — the little grip bottom-right is just the visible hint. The water re-flows around windows as you move and resize them.',
    find: q('.app-window-resize'),
    optional: true,
  },
  {
    title: 'Chat History',
    body: 'Every conversation, searchable. Click one to load it into the chat (even if the chat window is closed — it reopens), double-click a title to rename, × to delete, + New Chat for a fresh start.',
    find: windowByTitle('Chat History'),
    open: { label: 'Open Chat History', run: openWin('chat-history') },
  },
  {
    title: 'Neuron Graph',
    body: 'The activation graph behind the latest answer — which knowledge neurons fired and how strongly. It updates live as you chat.',
    find: windowByTitle('Neuron Graph'),
    open: { label: 'Open the Neuron Graph', run: openWin('chat-graph') },
  },
  {
    title: 'The dock',
    body: 'Minimized windows land here as pills — click to restore.',
    find: q('.window-dock'),
    optional: true,
  },
  {
    title: 'Demo chat setup',
    body: 'This public demo replays pre-recorded answers by default. Connect your own LLM API key here (it stays in your browser, sent only to your provider) to ask real questions against the demo corpus.',
    find: q('.demo-llm-pill'),
    optional: true,
  },
  {
    title: 'That\'s the tour',
    body: 'Good next taps: Knowledge → 3D Universe for the full graph in space, Agency Lab → Venture Graph for persistent work, and Evaluate → Pallium for system health. Reopen this walkthrough from the ? beside Settings.',
  },
];

export default function DemoHelper() {
  const [step, setStep] = useState<number | null>(null); // null = tour closed
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    const start = () => setStep(0);
    window.addEventListener(START_TOUR_EVENT, start);
    return () => window.removeEventListener(START_TOUR_EVENT, start);
  }, []);

  // Advance to the next/previous step, silently skipping optional steps
  // whose target isn't on screen.
  const seek = useCallback((from: number, dir: 1 | -1): number | null => {
    let i = from + dir;
    while (i >= 0 && i < STEPS.length) {
      const s = STEPS[i];
      if (!s.optional || s.find?.()) return i;
      i += dir;
    }
    return null;
  }, []);

  // Track the current target's rect while the tour is open (windows move).
  useEffect(() => {
    if (step === null) return;
    const update = () => {
      const el = STEPS[step].find?.();
      const r = el?.getBoundingClientRect() ?? null;
      setTargetRect(prev => {
        if (!r) return null;
        if (prev && Math.abs(prev.x - r.x) < 1 && Math.abs(prev.y - r.y) < 1
          && Math.abs(prev.width - r.width) < 1 && Math.abs(prev.height - r.height) < 1) return prev;
        return r;
      });
    };
    update();
    const t = window.setInterval(update, 250);
    return () => window.clearInterval(t);
  }, [step]);

  const current = step !== null ? STEPS[step] : null;
  const missing = current?.find !== undefined && targetRect === null;

  // Tooltip card placement: below the target if there's room, else above,
  // else centered.
  const cardStyle = (() => {
    if (step === null) return {};
    const W = 320, H = 190, M = 12;
    if (!targetRect) {
      return { left: Math.max(M, (window.innerWidth - W) / 2), top: Math.max(M, (window.innerHeight - H) / 2) };
    }
    const left = Math.max(M, Math.min(targetRect.left, window.innerWidth - W - M));
    const below = targetRect.bottom + M;
    const top = below + H < window.innerHeight ? below : Math.max(M, targetRect.top - H - M);
    return { left, top };
  })();

  return (
    <>
      {current && (
        <div className="tour-layer">
          {targetRect ? (
            <div
              className="tour-ring"
              style={{
                left: targetRect.left - 6,
                top: targetRect.top - 6,
                width: targetRect.width + 12,
                height: targetRect.height + 12,
              }}
            />
          ) : (
            <div className="tour-dim" />
          )}
          <div className="tour-card" style={cardStyle}>
            <div className="tour-card-header">
              <span>{current.title}</span>
              <button className="tour-close" onClick={() => setStep(null)} title="End tour">✕</button>
            </div>
            <p className="tour-body">{current.body}</p>
            {missing && current.open && (
              <button className="tour-open" onClick={current.open.run}>
                {current.open.label} →
              </button>
            )}
            {missing && !current.open && current.hint && <p className="tour-hint">{current.hint}</p>}
            <div className="tour-footer">
              <span className="tour-count">{(step ?? 0) + 1} / {STEPS.length}</span>
              <div className="tour-btns">
                <button disabled={seek(step!, -1) === null} onClick={() => setStep(seek(step!, -1))}>Back</button>
                {seek(step!, 1) !== null
                  ? <button className="tour-next" onClick={() => setStep(seek(step!, 1))}>Next</button>
                  : <button className="tour-next" onClick={() => setStep(null)}>Done</button>}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
