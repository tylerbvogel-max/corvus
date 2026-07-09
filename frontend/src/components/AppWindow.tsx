import { useRef, useState, type ReactNode } from 'react';

/* Draggable / resizable floating window chrome for the desktop-style UI.
   Windows drag by their title bar, resize from the bottom-right handle,
   minimize to the dock, maximize (button, double-click, or drag-to-top
   snap), and snap to halves/quarters when dragged to screen edges.
   During a drag/resize gesture the element's style is mutated directly
   and the final rect is committed to React state on pointer-up — this
   keeps heavy page content from re-rendering per pointer event. */

export interface WinRect { x: number; y: number; w: number; h: number }

export interface WinState extends WinRect {
  z: number;
  min: boolean;
  max: boolean;
  /** Floating rect restored when un-maximizing (or torn off by drag). */
  restore?: WinRect;
}

interface AppWindowProps {
  title: string;
  state: WinState;
  focused: boolean;
  onFocus: () => void;
  onClose: () => void;
  onMinimize: () => void;
  onToggleMax: () => void;
  onCommit: (patch: Partial<WinState>) => void;
  children: ReactNode;
}

export const MIN_W = 360;
export const MIN_H = 240;
const SNAP_EDGE = 28;   // px from a screen edge that arms snapping
const DRAG_DEADZONE = 4;

type SnapZone = 'left' | 'right' | 'max' | 'tl' | 'tr' | 'bl' | 'br';
type ResizeDir = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
const RESIZE_DIRS: readonly ResizeDir[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];

function detectSnapZone(px: number, py: number): SnapZone | null {
  const vw = window.innerWidth, vh = window.innerHeight;
  const l = px < SNAP_EDGE, r = px > vw - SNAP_EDGE;
  const t = py < SNAP_EDGE, b = py > vh - SNAP_EDGE;
  if (l && t) return 'tl';
  if (r && t) return 'tr';
  if (l && b) return 'bl';
  if (r && b) return 'br';
  if (l) return 'left';
  if (r) return 'right';
  if (t) return 'max';
  return null;
}

function snapZoneRect(zone: SnapZone): WinRect {
  const vw = window.innerWidth, vh = window.innerHeight;
  const hw = Math.floor(vw / 2), hh = Math.floor(vh / 2);
  switch (zone) {
    case 'left': return { x: 8, y: 8, w: hw - 12, h: vh - 16 };
    case 'right': return { x: hw + 4, y: 8, w: hw - 12, h: vh - 16 };
    case 'tl': return { x: 8, y: 8, w: hw - 12, h: hh - 12 };
    case 'tr': return { x: hw + 4, y: 8, w: hw - 12, h: hh - 12 };
    case 'bl': return { x: 8, y: hh + 4, w: hw - 12, h: hh - 12 };
    case 'br': return { x: hw + 4, y: hh + 4, w: hw - 12, h: hh - 12 };
    case 'max': return { x: 8, y: 8, w: vw - 16, h: vh - 16 };
  }
}

const wakeRefresh = () => window.dispatchEvent(new Event('corvus-wake-refresh'));

export default function AppWindow({
  title, state, focused, onFocus, onClose, onMinimize, onToggleMax, onCommit, children,
}: AppWindowProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [snapPreview, setSnapPreview] = useState<WinRect | null>(null);

  const onTitleDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    // The min/max/close buttons handle their own clicks — never start a drag from them.
    if ((e.target as HTMLElement).closest('button')) return;
    const el = rootRef.current;
    if (!el) return;
    let startX = e.clientX, startY = e.clientY;
    // Dragging a maximized window tears it off at its floating size first.
    const floating: WinRect = state.max
      ? { ...(state.restore ?? { x: 80, y: 60, w: 900, h: 600 }) }
      : { x: state.x, y: state.y, w: state.w, h: state.h };
    let torn = !state.max;
    let moved = false;
    let zone: SnapZone | null = null;
    let fx = floating.x, fy = floating.y; // live floating position
    document.body.style.userSelect = 'none'; // no text selection mid-drag

    const onMove = (ev: PointerEvent) => {
      if (!moved && Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_DEADZONE) return;
      moved = true;
      ev.preventDefault();
      if (!torn) {
        torn = true;
        // Center the restored title bar under the pointer.
        floating.x = Math.round(ev.clientX - floating.w / 2);
        floating.y = ev.clientY - 14;
        startX = ev.clientX; startY = ev.clientY;
        el.classList.remove('app-window--max');
        el.style.width = floating.w + 'px';
        el.style.height = floating.h + 'px';
      }
      const vw = window.innerWidth, vh = window.innerHeight;
      fx = Math.max(80 - floating.w, Math.min(floating.x + ev.clientX - startX, vw - 80));
      fy = Math.max(0, Math.min(floating.y + ev.clientY - startY, vh - 40));
      el.style.left = fx + 'px';
      el.style.top = fy + 'px';
      zone = detectSnapZone(ev.clientX, ev.clientY);
      setSnapPreview(zone ? snapZoneRect(zone) : null);
    };

    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.userSelect = '';
      setSnapPreview(null);
      if (!moved) return;
      if (zone === 'max') {
        onCommit({ ...floating, x: fx, y: fy, max: true, restore: { ...floating, x: fx, y: fy } });
      } else if (zone) {
        onCommit({ ...snapZoneRect(zone), max: false, restore: { ...floating, x: fx, y: fy } });
      } else {
        onCommit({ x: fx, y: fy, w: floating.w, h: floating.h, max: false });
      }
      wakeRefresh();
    };

    onFocus();
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Any edge or corner resizes; west/north edges move the window origin so
  // the opposite edge stays anchored. Text selection is suppressed for the
  // whole gesture (preventDefault + body user-select), otherwise the drag
  // highlights the window's content.
  const startResize = (dir: ResizeDir) => (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const el = rootRef.current;
    if (!el) return;
    const sx = e.clientX, sy = e.clientY;
    const o = { x: state.x, y: state.y, w: state.w, h: state.h };
    const r = { ...o };
    document.body.style.userSelect = 'none';
    const onMove = (ev: PointerEvent) => {
      ev.preventDefault();
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (dir.includes('e')) r.w = Math.max(MIN_W, o.w + dx);
      if (dir.includes('s')) r.h = Math.max(MIN_H, o.h + dy);
      if (dir.includes('w')) { r.w = Math.max(MIN_W, o.w - dx); r.x = o.x + o.w - r.w; }
      if (dir.includes('n')) { r.h = Math.max(MIN_H, o.h - dy); r.y = o.y + o.h - r.h; }
      el.style.left = r.x + 'px';
      el.style.top = r.y + 'px';
      el.style.width = r.w + 'px';
      el.style.height = r.h + 'px';
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.userSelect = '';
      if (r.x !== o.x || r.y !== o.y || r.w !== o.w || r.h !== o.h) onCommit({ ...r });
      wakeRefresh();
    };
    onFocus();
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  return (
    <div
      ref={rootRef}
      className={`app-window${state.max ? ' app-window--max' : ''}${focused ? ' app-window--focused' : ''}`}
      style={{
        left: state.x, top: state.y, width: state.w, height: state.h,
        zIndex: state.z,
        display: state.min ? 'none' : undefined,
      }}
      data-wake-obstacle
      onPointerDownCapture={onFocus}
    >
      <div className="app-window-titlebar" onPointerDown={onTitleDown} onDoubleClick={onToggleMax}>
        <span className="app-window-title">{title}</span>
        <div className="app-window-btns">
          <button onClick={onMinimize} title="Minimize to dock">─</button>
          <button onClick={onToggleMax} title={state.max ? 'Restore' : 'Maximize'}>{state.max ? '❐' : '□'}</button>
          <button className="app-window-close" onClick={onClose} title="Close">✕</button>
        </div>
      </div>
      <div className="app-window-body">{children}</div>
      {!state.max && (
        <>
          {RESIZE_DIRS.map(d => (
            <div key={d} className={`app-window-edge app-window-edge--${d}`} onPointerDown={startResize(d)} />
          ))}
          {/* Visible corner grip (functionally the same as the se edge) */}
          <div className="app-window-resize" onPointerDown={startResize('se')} title="Resize" />
        </>
      )}
      {snapPreview && (
        <div
          className="snap-preview"
          style={{ left: snapPreview.x, top: snapPreview.y, width: snapPreview.w, height: snapPreview.h }}
        />
      )}
    </div>
  );
}
