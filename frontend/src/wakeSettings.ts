/* Runtime-tunable settings for the ASCII wake desktop substrate.
   Defaults are the values hand-tuned in the playground
   (experiments/ascii-wake/index.html); the Settings popup (gear on the
   nav panel) exposes the same knobs live. Saving dispatches
   `corvus-wake-settings-changed`, which AsciiWake listens to — most
   knobs apply immediately, grid-shape ones (cell/substrate) rebuild. */

export interface WakeSettings {
  cell: number;            // char cell width px; height = cell * aspect
  aspect: number;
  damping: number;         // energy kept per sim step (trail length)
  substeps: number;        // sim steps per frame (wave speed)
  gain: number;            // wave height → character brightness
  radius: number;          // splat radius around the pointer (cells)
  strength: number;        // splat amplitude
  speedRef: number;        // px/event of mouse speed for 1× strength
  substrate: number;       // fraction of cells with a resting char
  substrateAlpha: number;  // resting texture brightness
  ramp: string;            // calm → crest characters (first is space)
  pad: number;             // px inflation around obstacles (data-wake-pad overrides)
  rain: boolean;           // ambient random drops
  rainEvery: number;       // frames between ambient drops
}

export const WAKE_RAMPS: readonly string[] = [
  ' .,-~:;=!*#%@',
  ' ·:;+=*xX#@',
  ' ·∙•oO0@',
  " `'·:*∘○●",
];

export const WAKE_DEFAULTS: WakeSettings = {
  cell: 7,
  aspect: 1.9,
  damping: 0.952,
  substeps: 1,
  gain: 3.4,
  radius: 1,
  strength: 0.5,
  speedRef: 36,
  substrate: 0.05,
  substrateAlpha: 0.21,
  ramp: WAKE_RAMPS[0],
  pad: 6,
  rain: true,
  rainEvery: 50,
};

const STORE_KEY = 'corvus-wake-settings';
export const WAKE_SETTINGS_EVENT = 'corvus-wake-settings-changed';

export function loadWakeSettings(): WakeSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? '');
    if (saved && typeof saved === 'object') return { ...WAKE_DEFAULTS, ...saved };
  } catch { /* fall through to defaults */ }
  return { ...WAKE_DEFAULTS };
}

export function saveWakeSettings(s: WakeSettings): void {
  localStorage.setItem(STORE_KEY, JSON.stringify(s));
  window.dispatchEvent(new Event(WAKE_SETTINGS_EVENT));
}

export function resetWakeSettings(): WakeSettings {
  localStorage.removeItem(STORE_KEY);
  window.dispatchEvent(new Event(WAKE_SETTINGS_EVENT));
  return { ...WAKE_DEFAULTS };
}
