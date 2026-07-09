import { useState } from 'react';
import {
  WAKE_RAMPS, loadWakeSettings, saveWakeSettings, resetWakeSettings,
  type WakeSettings,
} from '../wakeSettings';

/* "ASCII Wake" section of the Settings popup — the same knobs as the
   tuning playground (experiments/ascii-wake/index.html), applied live
   to the desktop substrate and persisted in localStorage. */

interface SliderSpec {
  key: keyof WakeSettings;
  label: string;
  min: number;
  max: number;
  step: number;
}

const SLIDERS: SliderSpec[] = [
  { key: 'cell', label: 'Cell size', min: 7, max: 18, step: 1 },
  { key: 'damping', label: 'Damping', min: 0.90, max: 0.995, step: 0.001 },
  { key: 'substeps', label: 'Wave speed', min: 1, max: 3, step: 1 },
  { key: 'gain', label: 'Brightness', min: 0.4, max: 4, step: 0.1 },
  { key: 'radius', label: 'Wake radius', min: 1, max: 8, step: 0.5 },
  { key: 'strength', label: 'Wake strength', min: 0.1, max: 3, step: 0.05 },
  { key: 'speedRef', label: 'Speed sensit.', min: 4, max: 60, step: 1 },
  { key: 'substrate', label: 'Substrate %', min: 0, max: 0.6, step: 0.01 },
  { key: 'substrateAlpha', label: 'Substrate dim', min: 0, max: 0.5, step: 0.01 },
  { key: 'pad', label: 'Object pad px', min: 0, max: 24, step: 1 },
];

export default function WakeSettingsPanel() {
  const [s, setS] = useState<WakeSettings>(loadWakeSettings);

  const update = (patch: Partial<WakeSettings>) => {
    const next = { ...s, ...patch };
    setS(next);
    saveWakeSettings(next);
  };

  return (
    <div className="wake-settings">
      {SLIDERS.map(spec => (
        <div key={spec.key} className="wake-settings-row">
          <label>{spec.label}</label>
          <input
            type="range"
            min={spec.min}
            max={spec.max}
            step={spec.step}
            value={s[spec.key] as number}
            onChange={e => update({ [spec.key]: parseFloat(e.target.value) })}
          />
          <output>{s[spec.key]}</output>
        </div>
      ))}
      <div className="wake-settings-row">
        <label>Char ramp</label>
        <select
          value={WAKE_RAMPS.includes(s.ramp) ? s.ramp : WAKE_RAMPS[0]}
          onChange={e => update({ ramp: e.target.value })}
        >
          {WAKE_RAMPS.map(r => (
            <option key={r} value={r}>{r.trim()}</option>
          ))}
        </select>
      </div>
      <div className="wake-settings-row wake-settings-check">
        <input
          id="wake-rain"
          type="checkbox"
          checked={s.rain}
          onChange={e => update({ rain: e.target.checked })}
        />
        <label htmlFor="wake-rain">Ambient drops</label>
      </div>
      <button
        className="wake-settings-reset"
        onClick={() => setS(resetWakeSettings())}
      >
        Reset wake to defaults
      </button>
    </div>
  );
}
