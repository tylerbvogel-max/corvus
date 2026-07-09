import { useEffect, useRef } from 'react';
import { loadWakeSettings, WAKE_SETTINGS_EVENT } from '../wakeSettings';

/* ═══════════════════════════════════════════════════════════════════
   AsciiWake — interactive ASCII "water wake" desktop substrate.

   A canvas filling its parent (the app's desktop layer) renders a grid
   of monospace characters driven by a classic two-buffer wave
   simulation. Mouse movement pushes a wake into the water (scaled by
   mouse speed), clicks drop a splash, and occasional ambient drops
   keep it alive. Characters are NEVER drawn where UI sits: any element
   in the document carrying `data-wake-obstacle` (floating windows, the
   nav panel, the dock) is masked out — padded by the `pad` setting, or
   a per-element `data-wake-pad="N"` override — and waves reflect off
   those masked regions like rocks in a pond. The mask refreshes on a
   short interval and immediately on a `corvus-wake-refresh` window
   event (dispatched after window drags/resizes and layout changes).
   The palette is read from the active theme's CSS variables, so it
   follows theme switches automatically.

   ── TUNING ──────────────────────────────────────────────────────
   All knobs live in src/wakeSettings.ts (defaults = the values
   hand-tuned in the playground at experiments/ascii-wake/index.html,
   2026-07-08) and are adjustable at runtime from the Settings popup
   (gear on the nav panel), persisted under localStorage key
   'corvus-wake-settings'. This component re-reads them on the
   `corvus-wake-settings-changed` event: grid-shape changes
   (cell/aspect/substrate) rebuild the simulation, everything else
   applies live. See WakeSettings in wakeSettings.ts for what each
   knob does.

   To re-tune from scratch: run the playground
   (`cd experiments/ascii-wake && python3 -m http.server 8040`), tune,
   "Copy JSON", and update WAKE_DEFAULTS — or just use the gear.
   Palette stops come from --accent-dim → --accent → --accent-light
   → --text of the active theme; to change the water color
   independently of the theme, replace readPaletteStops().
   Respects prefers-reduced-motion (renders nothing).
   ──────────────────────────────────────────────────────────────── */

const BUCKETS = 24;
const OBSTACLE_REFRESH_MS = 400; // catches layout shifts between explicit refresh events

function hexToRgb(raw: string): [number, number, number] | null {
  const m = raw.trim().match(/^#([0-9a-f]{6})$/i);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function readPaletteStops(): [number, number, number][] {
  const style = getComputedStyle(document.documentElement);
  const fallback: [number, number, number][] = [
    [138, 85, 37], [200, 117, 51], [212, 145, 90], [212, 207, 198],
  ];
  return (['--accent-dim', '--accent', '--accent-light', '--text'] as const)
    .map((v, i) => hexToRgb(style.getPropertyValue(v)) ?? fallback[i]);
}

// Deterministic PRNG so the resting substrate pattern is stable across rebuilds.
function mulberry32(seed: number): () => number {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export default function AsciiWake() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = canvas?.parentElement;
    if (!canvas || !container) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Mutable settings object: closures below always read the latest
    // values; the settings-changed listener updates it in place.
    const cfg = loadWakeSettings();

    let W = 0, H = 0, cellW = 0, cellH = 0;
    let curr = new Float32Array(0);
    let prev = new Float32Array(0);
    let blocked = new Uint8Array(0);
    let substrateMask = new Uint8Array(0);
    let palette: string[] = [];

    const buildPalette = () => {
      const stops = readPaletteStops();
      palette = [];
      for (let b = 0; b < BUCKETS; b++) {
        const t = b / (BUCKETS - 1);
        const seg = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
        const f = t * (stops.length - 1) - seg;
        const c = stops[seg].map((v, k) => Math.round(v + (stops[seg + 1][k] - v) * f));
        const a = 0.14 + 0.86 * t;
        palette.push(`rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`);
      }
    };

    const computeObstacles = () => {
      blocked.fill(0);
      const base = container.getBoundingClientRect();
      // Document-wide: floating windows/nav/dock are siblings of the
      // desktop layer, not children of it.
      document.querySelectorAll('[data-wake-obstacle]').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        const pad = Number(el.getAttribute('data-wake-pad') ?? cfg.pad);
        const x0 = Math.max(0, Math.floor((r.left - base.left - pad) / cellW));
        const x1 = Math.min(W - 1, Math.ceil((r.right - base.left + pad) / cellW));
        const y0 = Math.max(0, Math.floor((r.top - base.top - pad) / cellH));
        const y1 = Math.min(H - 1, Math.ceil((r.bottom - base.top + pad) / cellH));
        for (let y = y0; y <= y1; y++)
          for (let x = x0; x <= x1; x++) blocked[y * W + x] = 1;
      });
    };

    const rebuild = () => {
      const dpr = window.devicePixelRatio || 1;
      const cw = container.clientWidth, ch = container.clientHeight;
      if (cw === 0 || ch === 0) return;
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
      canvas.style.width = cw + 'px';
      canvas.style.height = ch + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      cellW = cfg.cell;
      cellH = Math.round(cfg.cell * cfg.aspect);
      W = Math.ceil(cw / cellW);
      H = Math.ceil(ch / cellH);
      curr = new Float32Array(W * H);
      prev = new Float32Array(W * H);
      blocked = new Uint8Array(W * H);
      substrateMask = new Uint8Array(W * H);
      const rand = mulberry32(1337);
      for (let i = 0; i < W * H; i++) substrateMask[i] = rand() < cfg.substrate ? 1 : 0;
      computeObstacles();
    };

    // Two-buffer wave equation; blocked cells are held at zero so waves
    // reflect off UI surfaces instead of passing beneath them.
    const simStep = () => {
      for (let y = 1; y < H - 1; y++) {
        const row = y * W;
        for (let x = 1; x < W - 1; x++) {
          const i = row + x;
          if (blocked[i]) { prev[i] = 0; continue; }
          let v = ((curr[i - 1] + curr[i + 1] + curr[i - W] + curr[i + W]) * 0.5 - prev[i]) * cfg.damping;
          if (v > -0.0004 && v < 0.0004) v = 0;
          prev[i] = v;
        }
      }
      const t = curr; curr = prev; prev = t;
    };

    const splat = (gx: number, gy: number, r: number, amp: number) => {
      const x0 = Math.max(1, Math.floor(gx - r)), x1 = Math.min(W - 2, Math.ceil(gx + r));
      const y0 = Math.max(1, Math.floor(gy - r)), y1 = Math.min(H - 2, Math.ceil(gy + r));
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          const dx = x - gx, dy = y - gy;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d > r) continue;
          const i = y * W + x;
          if (blocked[i]) continue;
          curr[i] += amp * (1 - d / r);
        }
      }
    };

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = `${Math.round(cellH * 0.82)}px ui-monospace, "Cascadia Mono", Menlo, monospace`;
      const ramp = cfg.ramp;
      const rampMax = ramp.length - 1;
      const halfW = cellW / 2, halfH = cellH / 2;
      let bucketCache = -1;
      for (let y = 0; y < H; y++) {
        const py = y * cellH + halfH;
        const row = y * W;
        for (let x = 0; x < W; x++) {
          const i = row + x;
          if (blocked[i]) continue; // never draw where UI lives
          let intensity = Math.min(1, Math.abs(curr[i]) * cfg.gain);
          if (intensity < 0.03) {
            if (!substrateMask[i]) continue;
            intensity = cfg.substrateAlpha;
          } else if (substrateMask[i] && intensity < cfg.substrateAlpha) {
            intensity = cfg.substrateAlpha;
          }
          const ch = ramp[Math.min(rampMax, Math.max(1, Math.round(intensity * rampMax)))];
          const bucket = Math.min(BUCKETS - 1, Math.floor(intensity * BUCKETS));
          if (bucket !== bucketCache) { ctx.fillStyle = palette[bucket]; bucketCache = bucket; }
          ctx.fillText(ch, x * cellW + halfW, py);
        }
      }
    };

    // ── Input (window-level: pointer moves anywhere on the desktop drive
    // the wake; splats over windows/nav are masked out by blocked cells)
    let lastX: number | null = null, lastY: number | null = null;
    const toLocal = (e: PointerEvent) => {
      const r = container.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };
    const onMove = (e: PointerEvent) => {
      const [x, y] = toLocal(e);
      if (lastX !== null && lastY !== null) {
        const dx = x - lastX, dy = y - lastY;
        const dist = Math.hypot(dx, dy);
        if (dist > 0.5) {
          const speedF = Math.min(2.5, Math.max(0.15, dist / cfg.speedRef));
          const steps = Math.max(1, Math.ceil(dist / (cellW * 0.9)));
          for (let s = 1; s <= steps; s++) {
            const px = lastX + dx * (s / steps), py = lastY + dy * (s / steps);
            splat(px / cellW, py / cellH, cfg.radius, -cfg.strength * speedF);
          }
        }
      }
      lastX = x; lastY = y;
    };
    const onDown = (e: PointerEvent) => {
      const [x, y] = toLocal(e);
      splat(x / cellW, y / cellH, cfg.radius * 2.2, -cfg.strength * 3.5);
    };
    const onLeave = () => { lastX = lastY = null; };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('blur', onLeave);

    // ── Lifecycle: resize, theme switches, settings changes, main loop
    const ro = new ResizeObserver(rebuild);
    ro.observe(container);
    const themeObs = new MutationObserver(buildPalette);
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const obstacleTimer = window.setInterval(computeObstacles, OBSTACLE_REFRESH_MS);
    window.addEventListener('corvus-wake-refresh', computeObstacles);

    const onSettings = () => {
      const next = loadWakeSettings();
      const needsRebuild = next.cell !== cfg.cell
        || next.aspect !== cfg.aspect
        || next.substrate !== cfg.substrate;
      Object.assign(cfg, next);
      if (needsRebuild) rebuild();
      else computeObstacles(); // pad may have changed
    };
    window.addEventListener(WAKE_SETTINGS_EVENT, onSettings);

    let raf = 0;
    let rainCountdown = cfg.rainEvery;
    const loop = () => {
      if (cfg.rain && --rainCountdown <= 0) {
        rainCountdown = cfg.rainEvery;
        splat(2 + Math.random() * (W - 4), 2 + Math.random() * (H - 4), 2.5, -1.2);
      }
      for (let s = 0; s < cfg.substeps; s++) simStep();
      draw();
      raf = requestAnimationFrame(loop);
    };

    buildPalette();
    rebuild();
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      window.clearInterval(obstacleTimer);
      ro.disconnect();
      themeObs.disconnect();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('blur', onLeave);
      window.removeEventListener('corvus-wake-refresh', computeObstacles);
      window.removeEventListener(WAKE_SETTINGS_EVENT, onSettings);
    };
  }, []);

  return <canvas ref={canvasRef} className="ascii-wake" aria-hidden="true" />;
}
