// Playwright smoke test against the real packaged binary (ci.yml's
// e2e-smoke job starts it and points PLAYWRIGHT_BASE_URL at it -- see
// playwright.config.ts). Exercises the golden path CI's earlier curl-only
// smoke test never touched: the UI actually rendering, a fresh login
// bootstrapping a workspace, claiming an identity, and creating a channel
// through real components (#254).

import { expect, test } from '@playwright/test';

// Same recovery phrase ci.yml's curl-based smoke test already logs in
// with -- any valid BIP-39 mnemonic bootstraps a fresh workspace on first
// login, so reusing it here needs no new fixture.
const RECOVERY_PHRASE =
	'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

test('login, claim an identity, and create a channel', async ({ page }) => {
	await page.goto('/');

	await page.getByLabel('Workspace recovery phrase (12 words)').fill(RECOVERY_PHRASE);
	await page.getByRole('button', { name: 'Enter workspace' }).click();

	await page.getByPlaceholder('e.g. Ada').fill('E2E Smoke');
	// Exact match: a workspace with existing humans also renders
	// "Continue as {name}" buttons, which would otherwise substring-match
	// this locator too.
	await page.getByRole('button', { name: 'Continue', exact: true }).click();

	const channelName = `e2e-smoke-${Date.now()}`;
	const channelInput = page.getByPlaceholder('new-channel');
	await expect(channelInput).toBeVisible();
	await channelInput.fill(channelName);
	await page.getByRole('button', { name: 'Add' }).click();

	await expect(page.getByRole('link', { name: `#${channelName}` })).toBeVisible();
});
