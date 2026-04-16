import { useEffect, useRef } from 'react';

/**
 * useUrlSync
 *
 * Generic hook for round-tripping a flat state object through window.location.hash.
 * Intended for making UI state shareable via URL and restorable via browser
 * back/forward. Not a general-purpose router — just hash fragment encoding.
 *
 * Hash format:  `#<namespace>?k1=v1&k2=v2&...`
 * Values are URI-encoded. Missing keys = codec returned null (use for defaults).
 *
 * Usage:
 *   const schema = { sel: codecs.nullableInt, dept: codecs.str, min: codecs.num };
 *   useUrlSync('universe', { sel, dept, min }, schema, {
 *     onRestore: (restored) => { ... apply restored keys to local state ... },
 *     pushOnKeys: ['sel'],  // trigger history.pushState when selection changes
 *   });
 */

export type UrlCodec<V> = {
  encode: (v: V) => string | null;  // null = omit from URL (treat as default)
  decode: (raw: string) => V | undefined;  // undefined = skip (keep caller's current value)
};

export type UrlSchema<T> = { [K in keyof T]: UrlCodec<T[K]> };

export const codecs = {
  str: {
    encode: (v: string) => (v ? v : null),
    decode: (raw: string) => raw,
  } as UrlCodec<string>,
  num: {
    encode: (v: number) => (Number.isFinite(v) ? String(v) : null),
    decode: (raw: string) => {
      const n = parseFloat(raw);
      return Number.isFinite(n) ? n : undefined;
    },
  } as UrlCodec<number>,
  int: {
    encode: (v: number) => (Number.isFinite(v) ? String(Math.trunc(v)) : null),
    decode: (raw: string) => {
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : undefined;
    },
  } as UrlCodec<number>,
  bool: {
    encode: (v: boolean) => (v ? '1' : '0'),
    decode: (raw: string) => raw === '1' || raw === 'true',
  } as UrlCodec<boolean>,
  nullableInt: {
    encode: (v: number | null) => (v == null ? null : String(v)),
    decode: (raw: string) => {
      if (!raw) return null;
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : null;
    },
  } as UrlCodec<number | null>,
  /** Comma-separated numbers — for camera tuples etc. Empty array encodes as null. */
  numArray: {
    encode: (v: number[]) => (v && v.length ? v.map(x => (Number.isFinite(x) ? +x.toFixed(3) : 0)).join(',') : null),
    decode: (raw: string) => {
      if (!raw) return undefined;
      const parts = raw.split(',').map(s => parseFloat(s));
      if (parts.some(p => !Number.isFinite(p))) return undefined;
      return parts;
    },
  } as UrlCodec<number[]>,
};

function parseHash(hash: string, namespace: string): Map<string, string> {
  const out = new Map<string, string>();
  if (!hash) return out;
  const stripped = hash.replace(/^#/, '');
  if (!stripped.startsWith(namespace)) return out;
  const q = stripped.indexOf('?');
  if (q < 0) return out;
  for (const pair of stripped.slice(q + 1).split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    const k = eq < 0 ? pair : pair.slice(0, eq);
    const v = eq < 0 ? '' : decodeURIComponent(pair.slice(eq + 1));
    out.set(decodeURIComponent(k), v);
  }
  return out;
}

function serialize(namespace: string, pairs: [string, string][]): string {
  if (pairs.length === 0) return `#${namespace}`;
  return `#${namespace}?` + pairs.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
}

function encodeAll<T extends Record<string, unknown>>(state: T, schema: UrlSchema<T>): [string, string][] {
  const out: [string, string][] = [];
  for (const key of Object.keys(schema) as (keyof T)[]) {
    const enc = (schema[key] as UrlCodec<T[keyof T]>).encode(state[key]);
    if (enc !== null) out.push([String(key), enc]);
  }
  return out;
}

function decodeAll<T extends Record<string, unknown>>(parsed: Map<string, string>, schema: UrlSchema<T>): Partial<T> {
  const out: Partial<T> = {};
  for (const key of Object.keys(schema) as (keyof T)[]) {
    const raw = parsed.get(String(key));
    if (raw === undefined) continue;
    const dec = (schema[key] as UrlCodec<T[keyof T]>).decode(raw);
    if (dec !== undefined) (out as Record<string, unknown>)[String(key)] = dec;
  }
  return out;
}

export function useUrlSync<T extends Record<string, unknown>>(
  namespace: string,
  state: T,
  schema: UrlSchema<T>,
  options: {
    onRestore?: (restored: Partial<T>) => void;
    pushOnKeys?: (keyof T)[];
    debounceMs?: number;
  } = {},
) {
  const { onRestore, pushOnKeys = [], debounceMs = 150 } = options;
  const restoredRef = useRef(false);
  const lastWrittenRef = useRef<string>('');
  const lastPushValsRef = useRef<Record<string, string | null>>({});
  // Keep latest onRestore accessible to the popstate listener without
  // re-binding the listener on every render (which would miss initial events).
  const onRestoreRef = useRef(onRestore);
  useEffect(() => { onRestoreRef.current = onRestore; });

  // Restore once on mount
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const parsed = parseHash(window.location.hash, namespace);
    lastWrittenRef.current = window.location.hash;
    if (parsed.size > 0 && onRestore) {
      const restored = decodeAll(parsed, schema);
      if (Object.keys(restored).length > 0) onRestore(restored);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [namespace]);

  // Write on any state change (debounced)
  const stateKey = JSON.stringify(state);
  useEffect(() => {
    if (!restoredRef.current) return;
    const handle = window.setTimeout(() => {
      const pairs = encodeAll(state, schema);
      const next = serialize(namespace, pairs);
      if (next === lastWrittenRef.current) return;

      // Determine push vs replace based on pushOnKeys
      const pushMap = new Map(pairs);
      const curPushVals: Record<string, string | null> = {};
      for (const k of pushOnKeys) curPushVals[String(k)] = pushMap.get(String(k)) ?? null;

      const prev = lastPushValsRef.current;
      const firstWrite = Object.keys(prev).length === 0;
      const shouldPush = !firstWrite && pushOnKeys.some(k => prev[String(k)] !== curPushVals[String(k)]);

      if (shouldPush) window.history.pushState(null, '', next);
      else window.history.replaceState(null, '', next);

      lastWrittenRef.current = next;
      lastPushValsRef.current = curPushVals;
    }, debounceMs);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [namespace, stateKey, debounceMs]);

  // popstate (browser back/forward) → re-read and invoke latest onRestore
  useEffect(() => {
    function onPop() {
      const cb = onRestoreRef.current;
      if (!cb) return;
      const parsed = parseHash(window.location.hash, namespace);
      const restored = decodeAll(parsed, schema);
      cb(restored);
      lastWrittenRef.current = window.location.hash;
    }
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [namespace]);
}
