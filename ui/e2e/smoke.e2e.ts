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

test('unlock, claim a name, and create a channel', async ({ page }) => {
	await page.goto('/');

	// Unlock (06-screens.md): the landing leads with two big choices —
	// entering an existing phrase lives behind "I already have a phrase".
	await page.getByRole('button', { name: 'I already have a phrase' }).click();
	await page.getByLabel('Workspace recovery phrase').fill(RECOVERY_PHRASE);
	await page.getByRole('button', { name: 'Enter workspace' }).click();

	// Name screen. Exact match: a workspace with existing humans also
	// renders "Continue as {name}" buttons, which would otherwise
	// substring-match this locator too.
	const nameInput = page.getByPlaceholder('e.g. Riley');
	const someoneNew = page.getByRole('button', { name: "I'm someone new" });
	await expect(nameInput.or(someoneNew).first()).toBeVisible();
	if (await someoneNew.isVisible()) {
		await someoneNew.click();
	}
	await nameInput.fill('E2E Smoke');
	await page.getByRole('button', { name: 'Continue', exact: true }).click();

	// Home renders inside the new shell; the channel list panel's "New
	// channel" button opens a sheet.
	const channelName = `e2e-smoke-${Date.now()}`;
	await page.getByRole('button', { name: 'New channel' }).first().click();
	await page.getByLabel('Name').fill(channelName);
	await page.getByRole('button', { name: 'Create channel' }).click();

	// Creating navigates into the channel page.
	await expect(page.getByRole('heading', { name: `#${channelName}` })).toBeVisible();
});
