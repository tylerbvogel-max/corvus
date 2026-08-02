import { useEffect, useState } from 'react';
import { fetchAvailableModels, type ModelOption } from '../api/knowledge_graph';

const TIER_ORDER = ['frontier', 'free'] as const;
const TIER_LABELS: Record<string, string> = {
  frontier: 'Frontier Labs',
  free: 'Free Tier',
};

/** Shared hook: fetches available LLM models from the backend on mount. */
export function useModels() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAvailableModels()
      .then(setModels)
      .catch(() => {
        // Fallback to the active Codex-primary roster if the endpoint is unavailable.
        setModels([
          // Prices must track MODEL_REGISTRY in backend/app/services/llm_provider.py.
          { display_name: 'codex-luna', provider: 'openai_codex', api_id: '', tier: 'frontier', input_price: 1, output_price: 6, context_window_tokens: 272_000, effective_model: 'codex-luna', effective_provider: 'openai_codex', is_primary: true },
          { display_name: 'codex-terra', provider: 'openai_codex', api_id: '', tier: 'frontier', input_price: 2.5, output_price: 15, context_window_tokens: 272_000, effective_model: 'codex-terra', effective_provider: 'openai_codex', is_primary: true },
          { display_name: 'codex-sol', provider: 'openai_codex', api_id: '', tier: 'frontier', input_price: 5, output_price: 30, context_window_tokens: 272_000, effective_model: 'codex-sol', effective_provider: 'openai_codex', is_primary: true },
        ]);
      })
      .finally(() => setLoading(false));
  }, []);

  /** Group models by tier for rendering optgroups. Ordered: Frontier → Free → Sample. */
  const grouped: Record<string, ModelOption[]> = {};
  for (const tier of TIER_ORDER) {
    const tierModels = models.filter(m => m.tier === tier);
    if (tierModels.length > 0) {
      grouped[TIER_LABELS[tier] ?? tier] = tierModels;
    }
  }
  // Catch any tier not in TIER_ORDER
  for (const m of models) {
    if (!TIER_ORDER.includes(m.tier as typeof TIER_ORDER[number])) {
      const label = m.tier;
      (grouped[label] ??= []).push(m);
    }
  }

  /** Just the display names of available chat models (no mode suffix). */
  const modelNames = models.map(m => m.display_name);

  return { models, grouped, modelNames, loading };
}
