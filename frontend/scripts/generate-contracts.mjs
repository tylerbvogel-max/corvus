#!/usr/bin/env node
/**
 * Generate the frontend's API contracts from the backend's committed OpenAPI
 * documents.
 *
 * SOURCE OF TRUTH is backend/tests/contracts/openapi.<tenant>.json — the
 * artifacts capability_snapshot.py writes and `--check` already guards. They
 * are used instead of a running server for three reasons: CI has no server,
 * they are byte-stable, and there is one PER TENANT, which is the whole point.
 * Composition is per-profile: corvus-mind mounts 211 method-level routes and
 * corvus-locomo mounts 78, so "the API" is not one surface, and a generator
 * that pretended otherwise would relocate the drift rather than remove it.
 *
 * Two artifacts are emitted into src/contracts/:
 *
 *   schema.d.ts        openapi-typescript output for the SUPERSET of tenants.
 *                      Types only — no runtime, no client, no framework. This
 *                      is the generated half of the contract.
 *
 *   capabilities.ts    a small runtime table: every route the superset
 *                      documents, the capability that owns it, and which
 *                      tenants grant it. This is what lets a caller answer
 *                      "is this route absent because the profile does not
 *                      grant it?" rather than reporting every ungranted call
 *                      as a network failure — the fail-soft distinction that
 *                      record 04 seam 1 made a design constraint after
 *                      SystemUseBanner's .catch(() => {}) silently removed
 *                      the AC-8 notification.
 *
 * Usage:
 *   node scripts/generate-contracts.mjs            write the artifacts
 *   node scripts/generate-contracts.mjs --check    fail if they are stale
 */
import { readFileSync, writeFileSync, readdirSync, mkdirSync, existsSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const CONTRACTS_IN = resolve(FRONTEND, '..', 'backend', 'tests', 'contracts')
const OUT_DIR = join(FRONTEND, 'src', 'contracts')
const check = process.argv.includes('--check')

// Router tag -> capability, as declared in app/composition/capabilities.py.
// The OpenAPI document already carries the router tag on every operation, so
// this is the only hand-maintained line between the two sides — and it is
// asserted below: an unknown tag is a hard error, not a silent "unknown".
const TAG_CAPABILITY = {
  memory: 'memory', capabilities: 'memory',
  query: 'knowledge_graph', neurons: 'knowledge_graph', engrams: 'knowledge_graph',
  lineage: 'knowledge_graph', regions: 'knowledge_graph',
  ingest: 'ingestion', 'document-ingest': 'ingestion',
  'reference-memory': 'ingestion', seeding: 'ingestion',
  proposals: 'governance', provenance: 'governance', integrity: 'governance',
  agents: 'governance', tools: 'governance',
  'admin-eval': 'evaluation', 'admin-labs': 'evaluation',
  admin: 'operator', operations: 'operator', architecture: 'operator',
  'Chat Sessions': 'operator', 'roadmap-ledgers': 'operator',
  'external-v1': 'external_api',
}

const tenants = readdirSync(CONTRACTS_IN)
  .filter((f) => f.startsWith('surface.') && f.endsWith('.json'))
  .map((f) => f.slice('surface.'.length, -'.json'.length))
  .sort()

if (!tenants.length) {
  console.error(`  no surface.*.json in ${CONTRACTS_IN} — run capability_snapshot.py --write`)
  process.exit(2)
}

// ── capability table ────────────────────────────────────────────────────────
const routes = new Map() // "METHOD /path" -> { method, path, capability, tenants[] }
const unknownTags = new Set()

for (const tenant of tenants) {
  const surface = JSON.parse(readFileSync(join(CONTRACTS_IN, `surface.${tenant}.json`), 'utf8'))
  for (const r of surface.routes) {
    const key = `${r.method} ${r.path}`
    let capability = null
    for (const t of r.tags ?? []) {
      if (t in TAG_CAPABILITY) { capability = TAG_CAPABILITY[t]; break }
      unknownTags.add(t)
    }
    if (!routes.has(key)) routes.set(key, { ...r, capability, tenants: [] })
    routes.get(key).tenants.push(tenant)
  }
}

if (unknownTags.size) {
  console.error(
    `\n  Unrecognised router tag(s): ${[...unknownTags].join(', ')}\n` +
      `  A new router was mounted without a capability mapping in this script.\n` +
      `  Add it to TAG_CAPABILITY here AND confirm it matches the RouterSpec in\n` +
      `  backend/app/composition/capabilities.py. Refusing to emit a table that\n` +
      `  would silently classify a real route as "unknown capability".\n`,
  )
  process.exit(2)
}

const sortedRoutes = [...routes.values()].sort(
  (a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method),
)

// Routes carrying no recognised tag at all (e.g. the bare health probe) are
// reported rather than emitted with a wrong owner.
const untagged = sortedRoutes.filter((r) => !r.capability)
const tagged = sortedRoutes.filter((r) => r.capability)

const capabilityFile = `/* GENERATED by scripts/generate-contracts.mjs — do not edit.
 * Source: backend/tests/contracts/surface.*.json (${tenants.join(', ')}).
 * Regenerate with \`npm run gen:contracts\`; CI fails on drift.
 *
 * Composition is per-tenant, so "the API" is not one surface. This table is
 * how the client tells an UNGRANTED route apart from a FAILED one.
 */

export type Capability =
${[...new Set(Object.values(TAG_CAPABILITY))].sort().map((c) => `  | '${c}'`).join('\n')}

export type TenantId =
${tenants.map((t) => `  | '${t}'`).join('\n')}

export interface RouteContract {
  readonly method: string
  readonly path: string
  readonly capability: Capability
  /** Tenants whose composed application mounts this route. */
  readonly tenants: readonly TenantId[]
}

/** Every capability-owned route documented by at least one tenant profile. */
export const ROUTE_CONTRACTS: readonly RouteContract[] = [
${tagged
  .map(
    (r) =>
      `  { method: '${r.method}', path: '${r.path}', capability: '${r.capability}', tenants: [${r.tenants
        .map((t) => `'${t}'`)
        .join(', ')}] },`,
  )
  .join('\n')}
]

/** Routes mounted with no capability-owning router tag. Unowned, not hidden. */
export const UNOWNED_ROUTES: readonly string[] = [
${untagged.map((r) => `  '${r.method} ${r.path}',`).join('\n')}
]

/** Capability that owns each route, keyed \`METHOD /path\`. */
export const ROUTE_CAPABILITY: Readonly<Record<string, Capability>> = Object.freeze(
  Object.fromEntries(ROUTE_CONTRACTS.map((r) => [\`\${r.method} \${r.path}\`, r.capability])),
)

/** Capabilities each tenant profile grants, derived from what it actually mounts. */
export const TENANT_CAPABILITIES: Readonly<Record<TenantId, readonly Capability[]>> = Object.freeze({
${tenants
  .map((t) => {
    const caps = [...new Set(tagged.filter((r) => r.tenants.includes(t)).map((r) => r.capability))].sort()
    return `  '${t}': [${caps.map((c) => `'${c}'`).join(', ')}],`
  })
  .join('\n')}
})

/**
 * True when the route is documented by SOME tenant but not granted by this one.
 *
 * A caller that gets \`true\` here should not render a network error: the
 * surface was composed away on purpose, and the page should not have been
 * reachable in the first place. A caller that gets \`false\` and still failed
 * has a real failure worth surfacing.
 */
export function isComposedAway(method: string, path: string, tenant: TenantId): boolean {
  const contract = ROUTE_CONTRACTS.find((r) => r.method === method && r.path === path)
  return contract ? !contract.tenants.includes(tenant) : false
}
`

// ── types ───────────────────────────────────────────────────────────────────
// Superset document: every tenant's paths merged. Generating per tenant would
// give the UI N incompatible type worlds for routes that are byte-identical.
const merged = {
  openapi: '3.1.0',
  info: { title: 'Corvus (all tenants)', version: '0' },
  paths: {},
  components: { schemas: {} },
}
for (const tenant of tenants) {
  const doc = JSON.parse(readFileSync(join(CONTRACTS_IN, `openapi.${tenant}.json`), 'utf8'))
  for (const [p, item] of Object.entries(doc.paths)) {
    merged.paths[p] = { ...(merged.paths[p] ?? {}), ...item }
  }
  Object.assign(merged.components.schemas, doc.components?.schemas ?? {})
}
merged.paths = Object.fromEntries(Object.entries(merged.paths).sort(([a], [b]) => a.localeCompare(b)))
merged.components.schemas = Object.fromEntries(
  Object.entries(merged.components.schemas).sort(([a], [b]) => a.localeCompare(b)),
)

const ast = await openapiTS(merged)
const schemaFile =
  `/* GENERATED by scripts/generate-contracts.mjs — do not edit.\n` +
  ` * Source: backend/tests/contracts/openapi.*.json (${tenants.join(', ')}), merged.\n` +
  ` * Types only: no runtime, no client, no framework. Regenerate with\n` +
  ` * \`npm run gen:contracts\`; CI fails on drift.\n */\n\n` +
  astToString(ast)

// ── write or check ──────────────────────────────────────────────────────────
const artifacts = [
  [join(OUT_DIR, 'capabilities.ts'), capabilityFile],
  [join(OUT_DIR, 'schema.d.ts'), schemaFile],
]

if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true })

let stale = 0
for (const [file, content] of artifacts) {
  const current = existsSync(file) ? readFileSync(file, 'utf8') : null
  if (current === content) continue
  stale++
  if (check) console.error(`  STALE: ${file.replace(FRONTEND + '/', '')}`)
  else writeFileSync(file, content)
}

const summary =
  `${tagged.length} owned routes (+${untagged.length} unowned), ${tenants.length} tenants ` +
  `(${tenants.map((t) => `${t}: ${tagged.filter((r) => r.tenants.includes(t)).length}`).join(', ')})`

if (check) {
  if (stale) {
    console.error(
      `\n  Generated contracts are stale (${stale} file(s)).\n` +
        `  The backend contract moved and the frontend was not regenerated.\n` +
        `  Run \`npm run gen:contracts\` and commit the result.\n`,
    )
    process.exit(1)
  }
  console.log(`  contracts up to date — ${summary}`)
} else {
  console.log(`  wrote ${artifacts.length} artifact(s) — ${summary}`)
}
