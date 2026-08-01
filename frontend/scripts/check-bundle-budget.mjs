#!/usr/bin/env node
/**
 * Fail the build when the shipped bundle outgrows its measured budget.
 *
 * Reads dist/.vite/manifest.json rather than scraping Vite's console report,
 * so the "initial route" is derived from the entry's own static import graph
 * instead of from chunk filenames that change with every content hash.
 *
 * Run AFTER `npm run build`:
 *     node scripts/check-bundle-budget.mjs
 *     node scripts/check-bundle-budget.mjs --json     machine-readable report
 *
 * Budgets and their justifications live in bundle-budget.json. This script
 * only measures and compares; it never edits the budget.
 */
import { gzipSync } from 'node:zlib'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(FRONTEND, 'dist')
const MANIFEST = join(DIST, '.vite', 'manifest.json')
const BUDGET_FILE = join(FRONTEND, 'bundle-budget.json')
const asJson = process.argv.includes('--json')

function die(msg) {
  console.error(`\n  bundle budget: ${msg}\n`)
  process.exit(2)
}

if (!existsSync(MANIFEST)) {
  die(`no build manifest at ${MANIFEST}.\n  Run \`npm run build\` first (vite.config.ts must keep build.manifest = true).`)
}

const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'))
const budget = JSON.parse(readFileSync(BUDGET_FILE, 'utf8'))

/** gzip size of one emitted asset, in bytes. */
const gzipOf = (file) => gzipSync(readFileSync(join(DIST, file))).length

/**
 * Everything a cold visitor downloads before first render: the entry chunk,
 * every chunk it STATICALLY imports (manifest `imports`, transitively), and
 * all CSS those chunks pull in. `dynamicImports` are deliberately excluded —
 * they are the lazy leaves this record created.
 */
function initialRoute() {
  const entryKey = Object.keys(manifest).find((k) => manifest[k].isEntry)
  if (!entryKey) die('manifest has no entry chunk')
  const seen = new Set()
  const files = new Set()
  const walk = (key) => {
    if (seen.has(key)) return
    seen.add(key)
    const node = manifest[key]
    if (!node) return
    files.add(node.file)
    for (const css of node.css ?? []) files.add(css)
    for (const dep of node.imports ?? []) walk(dep)
  }
  walk(entryKey)
  return [...files]
}

const initialFiles = initialRoute()
const initialJs = initialFiles.filter((f) => f.endsWith('.js'))
const initialCss = initialFiles.filter((f) => f.endsWith('.css'))

const allJs = [...new Set(Object.values(manifest).map((n) => n.file))].filter((f) =>
  f.endsWith('.js'),
)

const sum = (files) => files.reduce((n, f) => n + gzipOf(f), 0)

const chunkSizes = allJs
  .map((f) => ({ file: f, gzip: gzipOf(f) }))
  .sort((a, b) => b.gzip - a.gzip)

const measured = {
  initialRouteGzip: sum(initialFiles),
  maxChunkGzip: chunkSizes[0]?.gzip ?? 0,
  totalJsGzip: sum(allJs),
}

const results = Object.entries(budget.budgets).map(([name, spec]) => {
  const actual = measured[name]
  if (actual === undefined) die(`budget "${name}" has no corresponding measurement`)
  return {
    name,
    actual,
    limit: spec.limit,
    pass: actual <= spec.limit,
    pctOfLimit: (actual / spec.limit) * 100,
    rationale: spec.rationale,
  }
})

if (asJson) {
  console.log(JSON.stringify({ measured, results, chunks: chunkSizes }, null, 2))
} else {
  // Decimal kB, matching Vite's own build report exactly, so a number here can
  // be compared to a number there without a unit conversion in between.
  const kb = (n) => `${(n / 1000).toFixed(2)} kB`
  console.log('\n  Bundle budget (gzip)\n')
  console.log(`  initial route  ${initialJs.length} js + ${initialCss.length} css`)
  for (const r of results) {
    const mark = r.pass ? 'ok  ' : 'FAIL'
    console.log(
      `  ${mark} ${r.name.padEnd(18)} ${kb(r.actual).padStart(10)} / ${kb(r.limit).padStart(10)}` +
        `  (${r.pctOfLimit.toFixed(1)}% of limit)`,
    )
  }
  console.log(`\n  largest chunks:`)
  for (const c of chunkSizes.slice(0, 3)) console.log(`    ${kb(c.gzip).padStart(10)}  ${c.file}`)
}

const failed = results.filter((r) => !r.pass)
if (failed.length) {
  console.error(`\n  BUDGET EXCEEDED — ${failed.length} of ${results.length}\n`)
  for (const r of failed) {
    console.error(`  ${r.name}: ${r.actual} > ${r.limit} bytes gzip`)
    console.error(`    why this budget exists: ${r.rationale}`)
  }
  console.error(
    `\n  Fix the regression, or raise the limit in bundle-budget.json and say why\n` +
      `  in the same commit. Do not raise it silently.\n`,
  )
  process.exit(1)
}
if (!asJson) console.log('\n  all budgets pass\n')
