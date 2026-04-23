import { useEffect, useState } from 'react';
import { fetchAvailableModels, type ModelOption } from '../api';

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
        // Fallback to Anthropic models if endpoint unavailable
        setModels([
          { display_name: 'haiku', provider: 'anthropic', api_id: '', tier: 'frontier', input_price: 0.8, output_price: 4, context_window_tokens: 200_000 },
          { display_name: 'sonnet', provider: 'anthropic', api_id: '', tier: 'frontier', input_price: 3, output_price: 15, context_window_tokens: 200_000 },
          { display_name: 'opus', provider: 'anthropic', api_id: '', tier: 'frontier', input_price: 15, output_price: 75, context_window_tokens: 200_000 },
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
