import { useEffect, useState } from 'react';
import {
  PROVIDER_INFO, getDemoLLM, setDemoLLM, clearDemoLLM, callProvider,
  DEMO_LLM_CHANGED_EVENT, type DemoLLMConfig,
} from '../demo/shim';

/* BYOK setup for the public demo (demo builds only — lazy-loaded behind
   VITE_DEMO in App.tsx). A pill in the bottom-right shows the chat mode
   (replay vs live); the wizard lets a visitor connect their own LLM API
   key. The key is kept in the visitor's localStorage and sent only,
   directly, to their chosen provider — it never touches the demo host. */

export default function DemoKeyWizard() {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<DemoLLMConfig | null>(getDemoLLM);

  const [provider, setProvider] = useState<DemoLLMConfig['provider']>(cfg?.provider ?? 'openrouter');
  const [key, setKey] = useState(cfg?.key ?? '');
  const [model, setModel] = useState(cfg?.model ?? PROVIDER_INFO[cfg?.provider ?? 'openrouter'].defaultModel);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => setCfg(getDemoLLM());
    window.addEventListener(DEMO_LLM_CHANGED_EVENT, sync);
    return () => window.removeEventListener(DEMO_LLM_CHANGED_EVENT, sync);
  }, []);

  const pickProvider = (p: DemoLLMConfig['provider']) => {
    setProvider(p);
    setModel(PROVIDER_INFO[p].defaultModel);
    setError(null);
  };

  const save = async () => {
    const candidate: DemoLLMConfig = { provider, key: key.trim(), model: model.trim() };
    if (!candidate.key || !candidate.model) {
      setError('Key and model are both required.');
      return;
    }
    setTesting(true);
    setError(null);
    try {
      // One tiny live call proves key + model before we commit.
      await callProvider(candidate, 'Reply with the single word: ok');
      setDemoLLM(candidate);
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <button
        className={`demo-llm-pill${cfg ? ' demo-llm-pill--live' : ''}`}
        data-wake-obstacle
        onClick={() => setOpen(true)}
        title="Demo chat setup"
      >
        {cfg ? `● Live · ${PROVIDER_INFO[cfg.provider].label}` : '○ Replay mode — connect your own key'}
      </button>

      {open && (
        <>
          <div className="settings-overlay" onClick={() => setOpen(false)} />
          <div className="demo-llm-modal" data-wake-obstacle>
            <div className="settings-popup-header">
              <span>Demo chat setup</span>
              <button className="settings-popup-close" onClick={() => setOpen(false)}>&times;</button>
            </div>
            <div className="demo-llm-body">
              <p className="demo-llm-note">
                This public demo has no server. By default the chat replays
                pre-recorded answers. Connect your own LLM API key to ask
                real questions against the demo corpus:
              </p>
              <ul className="demo-llm-note demo-llm-disclosure">
                <li>Your key stays in <b>your browser's localStorage</b> and is sent only, directly, to the provider you pick — never to this site's host.</li>
                <li>Usage and rate limits are your own (free tiers are typically 10–40 requests/min).</li>
                <li>Answers are grounded in the demo's synthetic aerospace corpus via client-side retrieval.</li>
              </ul>

              <label className="settings-label">Provider</label>
              <div className="demo-llm-providers">
                {(Object.keys(PROVIDER_INFO) as DemoLLMConfig['provider'][]).map(p => (
                  <button
                    key={p}
                    className={`demo-llm-provider-btn${provider === p ? ' active' : ''}`}
                    onClick={() => pickProvider(p)}
                  >
                    {PROVIDER_INFO[p].label}
                  </button>
                ))}
              </div>

              <label className="settings-label">API key</label>
              <input
                type="password"
                className="demo-llm-input"
                value={key}
                onChange={e => setKey(e.target.value)}
                placeholder={PROVIDER_INFO[provider].keyHint}
                autoComplete="off"
              />

              <label className="settings-label">Model</label>
              <input
                type="text"
                className="demo-llm-input demo-llm-input--mono"
                value={model}
                onChange={e => setModel(e.target.value)}
              />

              {error && <div className="demo-llm-error">{error}</div>}

              <div className="demo-llm-actions">
                <button className="demo-llm-save" onClick={save} disabled={testing}>
                  {testing ? 'Testing key…' : 'Test & save'}
                </button>
                {cfg && (
                  <button
                    className="demo-llm-clear"
                    onClick={() => { clearDemoLLM(); setKey(''); setError(null); }}
                  >
                    Disconnect
                  </button>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
