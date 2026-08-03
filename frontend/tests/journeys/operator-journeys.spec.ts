/* Operator-critical journeys (roadmap record durability-frontend-contracts,
   verification #6). Every journey is double-asserted: the UI outcome AND the
   backend state read back through the API. Selectors are accessible/text
   anchors, never pixels. The fixture data these click is seeded by
   backend/tests/seed_journey_fixture.py into a throwaway database. */

import { test, expect } from '@playwright/test';
import { MIND_API, bootOperator, openNavItem, openWindowByKey } from './helpers';

test.describe.configure({ mode: 'serial' });

async function receiptCount(page: import('@playwright/test').Page): Promise<number> {
  const ledger = await (await page.request.get(`${MIND_API}/roadmap-ledgers/journey-fixture`)).json();
  const node = ledger.state.nodes.find((n: { id: string }) => n.id === 'journey-record-1');
  return node?.reconciliationHistory?.length ?? 0;
}

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

/* Runs after the reconcile journey above and depends on its state (serial
   mode, one worker): that test leaves journey-record-1 carrying a single
   PARTIAL receipt, so the record is not retired and can still be closed here.

   This exists because a real bug shipped through the gap it covers. The
   backend learned to accept a countersign on a closed record, but the dossier
   only rendered its reconcile button when reviewSignal(node) !== 'retired' —
   so the affordance vanished at exactly the moment it became meaningful, and
   a record that closes in the same call that writes its receipt sealed
   carrying an acceptance no human had given. Nothing caught it: every journey
   above stops at a PARTIAL receipt, which never retires anything. */
test('a closed record can still be countersigned, and only affirmed', async ({ page }) => {
  await bootOperator(page);
  await openNavItem(page, 'Steer', 'Roadmap Ledgers');

  await expect(page.getByRole('heading', { name: /Journey Fixture Project/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Expand Journey record' }).click();

  // Receipt counts are read, not assumed. Hard-coding them would couple this
  // test to whether the reconcile journey above ran first, which made an
  // isolated `--grep` run fail for a reason that had nothing to do with the
  // behaviour under test.
  const before = await receiptCount(page);
  const plural = (n: number) => `RECONCILIATION \\/ ${n} RECEIPT${n === 1 ? '' : 'S'}`;

  // Close the record: complete + verification passed is what retires it.
  await page.getByRole('button', { name: 'Reconcile outcome' }).click();
  await page.getByLabel('Disposition').selectOption('complete');
  await page.getByLabel(/Independent verifier passed/).check();
  await page.getByLabel('Result recap').fill('Closed so the countersign path can be exercised.');
  await page.getByLabel('Verifier', { exact: true }).fill('journey-verifier');
  await page.getByLabel('Accepted by').fill('journey-agent');
  // Evidence is required once a receipt claims completion, and it must carry a
  // commit-shaped token: done means committed, so a record cannot close while
  // its work exists only in a working tree. The fixture ledger has no
  // project_path, so the token is shape-checked but never resolved.
  await page.getByLabel(/^Evidence/).fill('commit deadbeefcafe1234 — journey fixture');
  await page.getByRole('button', { name: 'Accept receipt' }).click();
  await expect(page.getByText(new RegExp(plural(before + 1)))).toBeVisible({ timeout: 30_000 });

  // THE REGRESSION: the button must survive retirement, renamed for what it
  // now does. Before the fix it disappeared here and the record was unsignable.
  const countersign = page.getByRole('button', { name: 'Countersign', exact: true });
  await expect(countersign).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Reconcile outcome' })).toHaveCount(0);

  await countersign.click();
  await expect(page.getByRole('heading', { name: 'Countersign recorded outcome' })).toBeVisible();

  // The backend refuses a receipt that disagrees with the one on file, so the
  // UI must make that disagreement unreachable rather than explain it in a 409.
  await expect(page.getByLabel('Disposition')).toBeDisabled();
  await expect(page.getByLabel('Disposition')).toHaveValue('complete');
  await expect(page.getByLabel(/Independent verifier passed/)).toBeDisabled();
  await expect(page.getByLabel(/Independent verifier passed/)).toBeChecked();
  // Seeded from the prior receipt: this is the verifier being accepted.
  await expect(page.getByLabel('Verifier', { exact: true })).toHaveValue('journey-verifier');

  // Submittable the moment it opens. The recap seeds itself for a countersign,
  // because a required field left empty silently disables the submit button —
  // which is how this dialog got reported as "clicking does nothing". Only the
  // accepting name is typed, and nothing else is touched.
  const submit = page.locator('.rl-dialog footer button.primary');
  await expect(submit).toBeEnabled();
  await expect(page.locator('.rl-dialog .rl-form-hint')).toHaveCount(0);
  await expect(page.getByLabel('Result recap')).not.toHaveValue('');

  // ...and when something IS missing, the dialog says so instead of going quiet.
  await page.getByLabel('Accepted by').fill('');
  await expect(submit).toBeDisabled();
  await expect(page.locator('.rl-dialog .rl-form-hint')).toContainText('accepting name');

  await page.getByLabel('Accepted by').fill('journey-human');
  await expect(submit).toBeEnabled();
  await submit.click();

  await expect(page.getByText(new RegExp(plural(before + 2)))).toBeVisible({ timeout: 30_000 });

  // Backend state: the countersign affirms without re-litigating. Status stays
  // done, the outcome is unchanged, and completedAt keeps the instant the work
  // landed rather than the instant it was signed for.
  const ledger = await (await page.request.get(`${MIND_API}/roadmap-ledgers/journey-fixture`)).json();
  const node = ledger.state.nodes.find((n: { id: string }) => n.id === 'journey-record-1');
  expect(node.status).toBe('done');
  expect(node.reconciliationHistory).toHaveLength(before + 2);

  const closing = node.reconciliationHistory[before];
  const signed = node.reconciliationHistory[before + 1];
  expect(signed.acceptedBy).toBe('journey-human');
  expect(signed.verifier).toBe('journey-verifier');
  expect(signed.verifier).not.toBe(signed.acceptedBy);
  // Affirmation, not re-litigation.
  expect(signed.disposition).toBe(closing.disposition);
  expect(signed.verificationPassed).toBe(closing.verificationPassed);
  expect(node.completedAt).toBe(closing.acceptedAt);
});
