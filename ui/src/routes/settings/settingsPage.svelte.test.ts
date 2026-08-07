// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends only on $lib/api/settings.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SettingsPage from './+page.svelte';
import { settings, type WorkspaceSettings } from '$lib/api/settings';

vi.mock('$lib/api/settings', () => ({
	settings: { get: vi.fn(), update: vi.fn() }
}));

const loadedSettings: WorkspaceSettings = {
	'dispatcher.model_override': null,
	'dispatcher.fallback_enabled': true,
	'guard.turn_limit': 10,
	'guard.cycle_window': 8,
	'guard.cycle_threshold': 3,
	'guard.timeout_minutes': 30,
	'rivulet.summarization_enabled': true,
	'rivulet.context_threshold_pct': 80,
	'rivulet.recent_messages_kept': 20,
	'sync.eager_files_lan': true,
	'sync.eager_files_wan': false,
	'ui.port': 8484
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('settings/+page.svelte', () => {
	it('seeds the form fields from settings.get()', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);

		render(SettingsPage);

		await expect.element(page.getByText('Turn limit', { exact: false })).toBeInTheDocument();
		const turnLimitInput = page.getByRole('spinbutton', { name: /Turn limit/ });
		await expect.element(turnLimitInput).toHaveValue(10);
		const lanCheckbox = page.getByRole('checkbox', { name: 'Eager sync on LAN' });
		await expect.element(lanCheckbox).toBeChecked();
		const wanCheckbox = page.getByRole('checkbox', { name: 'Eager sync on WAN' });
		await expect.element(wanCheckbox).not.toBeChecked();
	});

	it('saves only the fields that changed', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(settings.update).mockResolvedValueOnce({ ...loadedSettings, 'guard.turn_limit': 25 });

		render(SettingsPage);
		const turnLimitInput = page.getByRole('spinbutton', { name: /Turn limit/ });
		await expect.element(turnLimitInput).toHaveValue(10);

		await turnLimitInput.fill('25');
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(settings.update).toHaveBeenCalledWith({ 'guard.turn_limit': 25 });
		await expect.element(page.getByText('Saved.')).toBeInTheDocument();
	});

	it('does not call settings.update when nothing changed', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);

		render(SettingsPage);
		await expect.element(page.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();

		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(settings.update).not.toHaveBeenCalled();
	});

	it('shows an error message when saving fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(settings.update).mockRejectedValueOnce(new Error('Failed to save settings'));

		render(SettingsPage);
		const turnLimitInput = page.getByRole('spinbutton', { name: /Turn limit/ });
		await expect.element(turnLimitInput).toHaveValue(10);
		await turnLimitInput.fill('50');
		await page.getByRole('button', { name: 'Save changes' }).click();

		await expect.element(page.getByText('Failed to save settings')).toBeInTheDocument();
	});

	it('shows a loading error when settings.get fails', async () => {
		vi.mocked(settings.get).mockRejectedValueOnce(new Error('Failed to load settings'));

		render(SettingsPage);

		await expect.element(page.getByText('Failed to load settings')).toBeInTheDocument();
	});
});
