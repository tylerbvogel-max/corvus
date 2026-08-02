// Verifies the fail-soft design constraint from roadmap record 05:
// the shared HTTP layer must tell "this route is composed away on this
// tenant profile" apart from "this call failed". Runs browser-less —
// classifyFailure and matchRouteContract are pure over the generated
// route table in src/contracts/capabilities.ts.
import { test, expect } from '@playwright/test';
import {
  classifyFailure,
  matchRouteContract,
  ComposedAwayError,
} from '../src/api/http';

test.describe('composed-away classification', () => {
  test('ungranted capability on this profile classifies as composed-away, not failure', () => {
    // governance routes are granted to corvus-mind only; corvus-locomo
    // composes them away. A 404 there is intent, not breakage.
    expect(classifyFailure('GET', '/admin/proposals/', 'corvus-locomo')).toBe('composed-away');
    // operator likewise (chat sessions are an operator surface).
    expect(classifyFailure('GET', '/chat/sessions/5', 'corvus-locomo')).toBe('composed-away');
  });

  test('granted route failure stays a real failure', () => {
    expect(classifyFailure('GET', '/admin/proposals/', 'corvus-mind')).toBe('failure');
    // knowledge_graph is granted to BOTH tenants.
    expect(classifyFailure('POST', '/query', 'corvus-locomo')).toBe('failure');
    expect(classifyFailure('POST', '/query', 'corvus-mind')).toBe('failure');
  });

  test('unknown tenant or uncontracted route classifies conservatively as failure', () => {
    expect(classifyFailure('GET', '/admin/proposals/', undefined)).toBe('failure');
    expect(classifyFailure('GET', '/admin/proposals/', 'corvus-aero')).toBe('failure');
    expect(classifyFailure('GET', '/no/such/route', 'corvus-mind')).toBe('failure');
  });

  test('concrete URLs match parameterized contracts, literals preferred', () => {
    expect(matchRouteContract('GET', '/chat/sessions/5')?.path).toBe('/chat/sessions/{session_id}');
    // query strings are stripped before matching
    expect(matchRouteContract('GET', '/admin/alerts?include_acknowledged=true')?.path).toBe('/admin/alerts');
    // a literal segment beats a parameter segment of the same shape
    expect(matchRouteContract('GET', '/admin/proposals/stats')?.path).toBe('/admin/proposals/stats');
    expect(matchRouteContract('GET', '/admin/proposals/42')?.path).toBe('/admin/proposals/{proposal_id}');
  });

  test('ComposedAwayError names the route, tenant, and intent', () => {
    const err = new ComposedAwayError('GET', '/admin/proposals/', 'corvus-locomo');
    expect(err.name).toBe('ComposedAwayError');
    expect(err.message).toContain('composed away');
    expect(err.message).toContain('corvus-locomo');
    expect(err.message).toContain('/admin/proposals/');
    expect(err instanceof Error).toBe(true);
  });
});
