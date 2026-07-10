/** Tenant configuration — fetched once from /tenant on app init. */

export interface SeedPrompt {
  label: string;
  text: string;
}

export interface TenantConfig {
  tenant_id: string;
  display_name: string;
  description: string;
  seed_prompts?: SeedPrompt[];
  /** True on memory tenants (corvus-mind): enables the Memory metrics
   *  page and hides the chat/Query Lab surfaces (no human chat there). */
  memory_surface?: boolean;
}

export interface TenantSummary {
  tenant_id: string;
  display_name: string;
  default_port: number | null;
}

let _cached: TenantConfig | null = null;
let _allCached: TenantSummary[] | null = null;

export async function fetchTenantConfig(): Promise<TenantConfig> {
  if (_cached) return _cached;
  try {
    const resp = await fetch('/tenant');
    if (resp.ok) {
      _cached = await resp.json();
      return _cached!;
    }
  } catch {
    // Fallback for dev/offline
  }
  _cached = {
    tenant_id: 'corvus-aero',
    display_name: 'Corvus Aero',
    description: 'Biomimetic neuron graph for aerospace defense prompt preparation.',
  };
  return _cached;
}

export async function fetchAllTenants(): Promise<TenantSummary[]> {
  if (_allCached) return _allCached;
  try {
    const resp = await fetch('/tenants');
    if (resp.ok) {
      _allCached = await resp.json();
      return _allCached!;
    }
  } catch {
    // Fallback
  }
  _allCached = [];
  return _allCached;
}

export function getTenantConfig(): TenantConfig | null {
  return _cached;
}
