// Memory capability adapter — the memory-organ read surfaces the console
// renders: aggregate metrics, trust trajectories, the human-judgment inbox,
// compiled skills, captured sessions, subscription utilization, and the
// scheduled-job and SLO health surfaces.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'memory'.
//
// This adapter closes a real boundary hole. Five components fetched these
// routes with bare `fetch()` — one of them hand-rolling its own retrying
// `fetchJson` — which meant they bypassed http.ts entirely and could not tell
// a route that was COMPOSED AWAY from one that FAILED. On a tenant without the
// memory capability that surfaces as an unexplained error instead of an
// honestly absent feature, which is the same class of failure as the AC-8
// banner disappearing silently.

import { ComposedAwayError, json } from './http';

/** Retry transient transport failures, never an ungranted route.
 *
 * The metrics dashboard polls and previously carried its own retrying
 * `fetchJson`. Retry belongs here, not in a component — but a
 * ComposedAwayError must pass straight through: retrying a route this tenant
 * was never granted burns three round trips to reach the same honest answer.
 */
async function retrying<T>(url: string, attempts = 3): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await json<T>(url);
    } catch (error) {
      if (error instanceof ComposedAwayError) throw error;
      lastError = error;
      if (attempt < attempts) {
        await new Promise(resolve => setTimeout(resolve, attempt * 500));
      }
    }
  }
  throw lastError;
}

// ── Aggregate metrics ──

/** Performance and growth for the memory organ. */
export function fetchMindMetrics<T = unknown>(): Promise<T> {
  return retrying<T>('/metrics/mind');
}

/** Per-lesson trust trajectories (utility over time). */
export async function fetchTrust<T = unknown>(): Promise<T[]> {
  const body = await retrying<{ lessons?: T[] }>('/metrics/mind/trust');
  return body.lessons ?? [];
}

/** Everything awaiting human judgment: findings, proposals, borderline pairs. */
export function fetchInbox<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/inbox');
}

/** Compiled skills with source health and rendered bodies. */
export function fetchSkills<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/skills');
}

/** Episode-log browser: captured/backfilled sessions plus distill status. */
export function fetchSessions<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/sessions');
}

/** LoCoMo certificate phase status, derived from run logs on disk. */
export function fetchLocomoRun<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/locomo-run');
}

// ── Subscription utilization ──

export function fetchSubscription<T = unknown>(): Promise<T> {
  return retrying<T>('/metrics/mind/subscription');
}

export function fetchCodexSubscription<T = unknown>(): Promise<T> {
  return retrying<T>('/metrics/mind/subscription/codex');
}

// ── Operational surfaces (durability-operational-envelope) ──

/** Scheduled-job inventory judged against its run receipts. */
export function fetchJobHealth<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/jobs');
}

/** Service-level objectives judged against live signals. */
export function fetchSlo<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/slo');
}

/** Delivery-pathway habituation ledger. */
export function fetchDeliveryPathways<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/delivery-pathways');
}

/** Load-bearing rate split by delivery channel. */
export function fetchInjectionChannels<T = unknown>(): Promise<T> {
  return json<T>('/metrics/mind/injection-channels');
}
