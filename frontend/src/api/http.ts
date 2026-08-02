// Shared HTTP layer for the capability adapters.
//
// This is the thin handwritten layer the generated contracts cannot express:
// auth header injection, error-body unwrapping, and — the fail-soft design
// constraint from record 05 — telling "this route is composed away on this
// tenant profile" apart from "this call failed". A composed-away route 404s
// like any missing path; without this distinction the UI renders a network
// error for a surface that was removed on purpose (the AC-8 banner
// disappearance was exactly this class).

// Statically imported. auth.ts is 60 lines of side-effect-free localStorage
// accessors, so it can never be split into its own chunk anyway: App.tsx and
// ProposalQueuePage.tsx both import it statically. Importing it dynamically
// here bought nothing and cost a dynamic-import round trip on EVERY request,
// while producing the build's mixed static/dynamic warning.
import { getAuthHeaders } from '../auth';
import { getTenantConfig } from '../config';
import {
  ROUTE_CONTRACTS,
  TENANT_CAPABILITIES,
  isComposedAway,
  type RouteContract,
  type TenantId,
} from '../contracts/capabilities';

export class ComposedAwayError extends Error {
  readonly method: string;
  readonly route: string;
  readonly tenant: string;

  constructor(method: string, route: string, tenant: string) {
    super(
      `${method} ${route} is composed away on the ${tenant} profile — ` +
      'this surface is not granted here, the call did not fail',
    );
    this.name = 'ComposedAwayError';
    this.method = method;
    this.route = route;
    this.tenant = tenant;
  }
}

/**
 * Match a concrete request URL back to its route contract. Path parameters
 * make this a segment walk, not a lookup: `/chat/sessions/5` must find
 * `/chat/sessions/{session_id}`. When a literal segment and a parameter
 * segment both match, the contract with more literal matches wins
 * (`/admin/documents/upload` beats `/admin/documents/{job_id}`).
 */
export function matchRouteContract(method: string, url: string): RouteContract | null {
  const path = url.split('?')[0];
  const segs = path.split('/');
  let best: RouteContract | null = null;
  let bestLiterals = -1;
  for (const contract of ROUTE_CONTRACTS) {
    if (contract.method !== method) continue;
    const rsegs = contract.path.split('/');
    if (rsegs.length !== segs.length) continue;
    let literals = 0;
    let ok = true;
    for (let i = 0; i < rsegs.length; i++) {
      if (rsegs[i].startsWith('{')) continue;
      if (rsegs[i] !== segs[i]) { ok = false; break; }
      literals++;
    }
    if (ok && literals > bestLiterals) { best = contract; bestLiterals = literals; }
  }
  return best;
}

function isKnownTenant(id: string | undefined): id is TenantId {
  return !!id && id in TENANT_CAPABILITIES;
}

/**
 * Pure classification of a failed call: composed away on this profile, or a
 * real failure. Exported separately from json() so it is testable without a
 * browser. Unknown tenants and routes outside the contract table classify as
 * 'failure' — the conservative reading, since we cannot prove intent.
 */
export function classifyFailure(
  method: string,
  url: string,
  tenantId: string | undefined,
): 'composed-away' | 'failure' {
  if (!isKnownTenant(tenantId)) return 'failure';
  const contract = matchRouteContract(method, url);
  if (!contract) return 'failure';
  return isComposedAway(contract.method, contract.path, tenantId)
    ? 'composed-away'
    : 'failure';
}

export async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const authHeaders = getAuthHeaders();
  const mergedInit: RequestInit = {
    ...init,
    headers: { ...authHeaders, ...(init?.headers || {}) },
  };
  const res = await fetch(url, mergedInit);
  if (!res.ok) {
    const method = (init?.method || 'GET').toUpperCase();
    if (classifyFailure(method, url, getTenantConfig()?.tenant_id) === 'composed-away') {
      const contract = matchRouteContract(method, url)!;
      throw new ComposedAwayError(method, contract.path, getTenantConfig()!.tenant_id);
    }
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (detail?.message) {
        const flags = detail.flags as Array<{ description: string; severity: string; pattern?: string }>;
        if (flags?.length) {
          const reasons = flags.map((f: { description: string; pattern?: string }) =>
            f.description + (f.pattern ? ` — "${f.pattern}"` : '')).join('; ');
          message = `${detail.message}: ${reasons}`;
        } else {
          message = detail.message;
        }
      } else if (typeof detail === 'string') {
        message = detail;
      }
    } catch {
      // Response body wasn't JSON — use status text
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}
