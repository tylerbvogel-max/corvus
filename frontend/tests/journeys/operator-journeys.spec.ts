/* Operator-critical journeys (roadmap record durability-frontend-contracts,
   verification #6). Every journey is double-asserted: the UI outcome AND the
   backend state read back through the API. Selectors are accessible/text
   anchors, never pixels. The fixture data these click is seeded by
   backend/tests/seed_journey_fixture.py into a throwaway database. */

import { test, expect } from '@playwright/test';
import { MIND_API, bootOperator, openNavItem, openWindowByKey } from './helpers';

test.describe.configure({ mode: 'serial' });

test('proposal approval auto-applies in one step', async ({ page }) => {
  await bootOperator(page);
  await openNavItem(page, 'Steer', 'Proposal Queue');

  // Select the seeded proposal (queue rows render id/origin/state, not the
  // gap description), then identify: the reviewer input renders inside the
  // Review Decision section, which mounts only for a selected 'proposed'
  // row. The name also becomes the X-Corvus-User identity the backend
  // records as reviewed_by/applied_by.
  await page.getByText('#1', { exact: true }).click();
  await page.getByPlaceholder('Your name (required)').fill('journey-operator');
  await page.getByRole('button', { name: /Approve & Apply/ }).click();

  // UI outcome: approval is one transaction (approve = apply), so both the
  // Review and Application sections must appear together.
  await expect(page.getByText(/Approved.* by journey-operator/).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Applied by journey-operator/).first()).toBeVisible({ timeout: 30_000 });

  // Backend state: terminal 'applied' — a lingering 'approved' is itself a
  // bug (one-step lifecycle since 2026-07-17).
  const detail = await (await page.request.get(`${MIND_API}/admin/proposals/1`)).json();
  expect(detail.state).toBe('applied');
  expect(detail.state).not.toBe('approved');
  expect(detail.applied_by).toBe('journey-operator');
  expect(detail.reviewed_by).toBe('journey-operator');

  // And the refinement genuinely landed on the graph.
  const neuron = await (await page.request.get(`${MIND_API}/neurons/9001`)).json();
  expect(neuron.summary).toBe('Summary refined by the operator journey spec.');
});

test('integrity finding resolves directly', async ({ page }) => {
  await bootOperator(page);
  // Integrity is editorially curated out of the memory-surface nav rail, so
  // reach it the way the product's own walkthrough does.
  await openWindowByKey(page, 'integrity-findings');

  // Target the row body by its description — the bare type label also lives
  // in the (invisible) filter dropdown options.
  await page.getByText('Journey fixture: seeded stale-content finding.').first().click();
  await page.getByPlaceholder('Your name').fill('journey-operator');
  await page.getByRole('button', { name: 'Mark Reviewed' }).click();

  // UI outcome, part 1: a direct resolve drains the default open-status
  // queue and clears the selection.
  await expect(page.getByText('No findings found.')).toBeVisible({ timeout: 30_000 });

  // UI outcome, part 2: the resolved finding carries its receipt — flip the
  // status filter to Resolved and reopen it.
  await page
    .locator('select', { has: page.locator('option[value="resolved"]') })
    .selectOption('resolved');
  await page.getByText('Journey fixture: seeded stale-content finding.').first().click();
  await expect(page.getByText(/Resolved as/).first()).toBeVisible();
  await expect(page.getByText(/by journey-operator/).first()).toBeVisible();

  // Backend state.
  const finding = await (await page.request.get(`${MIND_API}/admin/integrity/findings/1`)).json();
  expect(finding.status).toBe('resolved');
  expect(finding.resolution).toBe('reviewed');
  expect(finding.resolved_by).toBe('journey-operator');
});

test('roadmap record reconciliation renders a receipt', async ({ page }) => {
  await bootOperator(page);
  await openNavItem(page, 'Steer', 'Roadmap Ledgers');

  // The sole seeded ledger is auto-selected; assert it rather than clicking
  // (the sidebar button sits under the still-open nav flyout).
  await expect(page.getByRole('heading', { name: /Journey Fixture Project/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Expand Journey record' }).click();
  await page.getByRole('button', { name: 'Reconcile outcome' }).click();

  await page.getByLabel('Result recap').fill('Journey spec exercised the reconcile flow end to end.');
  // Verifier must differ from accepted_by — the backend rejects self-acceptance.
  await page.getByLabel('Verifier', { exact: true }).fill('journey-spec');
  await page.getByLabel('Accepted by').fill('journey-operator');
  await page.getByRole('button', { name: 'Accept receipt' }).click();

  // UI outcome: the dossier now carries exactly one receipt.
  await expect(page.getByText(/RECONCILIATION \/ 1 RECEIPT/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/verifier journey-spec/).first()).toBeVisible();

  // Backend state: revision bumped by the reconcile, receipt persisted on
  // the record with the submitted disposition.
  const ledger = await (await page.request.get(`${MIND_API}/roadmap-ledgers/journey-fixture`)).json();
  expect(ledger.revision).toBe(2);
  const node = ledger.state.nodes.find((n: { id: string }) => n.id === 'journey-record-1');
  expect(node.reconciliationHistory).toHaveLength(1);
  expect(node.reconciliationHistory[0].disposition).toBe('partial');
  expect(node.reconciliationHistory[0].verifier).toBe('journey-spec');
  expect(node.reconciliationHistory[0].acceptedBy).toBe('journey-operator');
});
