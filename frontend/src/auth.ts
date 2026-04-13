/** Access key management for the access gate middleware. */

const STORAGE_KEY = 'corvus-access-key';
const REVIEWER_KEY = 'corvus-reviewer-name';

export function getAccessKey(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setAccessKey(key: string): void {
  localStorage.setItem(STORAGE_KEY, key);
}

export function clearAccessKey(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function getReviewerName(): string {
  return localStorage.getItem(REVIEWER_KEY) || '';
}

export function setReviewerName(name: string): void {
  const trimmed = name.trim();
  if (trimmed) localStorage.setItem(REVIEWER_KEY, trimmed);
  else localStorage.removeItem(REVIEWER_KEY);
}

export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const key = getAccessKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  // Sent on every request: in RBAC disabled/header modes this sets the
  // resolved identity's user_id, which the server uses as authoritative
  // reviewed_by / applied_by on proposals. In azure_ad mode the JWT wins.
  const reviewer = getReviewerName();
  if (reviewer) headers['X-Corvus-User'] = reviewer;
  return headers;
}

/** Authenticated fetch wrapper — injects access key header automatically. */
export function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = { ...getAuthHeaders(), ...(init?.headers || {}) };
  return fetch(url, { ...init, headers });
}

/** Check if the server requires an access key and if ours is valid. */
export async function checkAccess(): Promise<'open' | 'valid' | 'invalid'> {
  try {
    // Try an authenticated request to a non-exempt endpoint
    const res = await fetch('/neurons/stats', {
      headers: getAuthHeaders(),
    });
    if (res.ok) return getAccessKey() ? 'valid' : 'open';
    if (res.status === 401) return 'invalid';
    return 'open'; // other errors — assume open
  } catch {
    return 'open';
  }
}
