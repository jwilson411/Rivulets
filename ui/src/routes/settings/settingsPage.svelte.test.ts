// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/settings and $lib/api/dispatch (#31's hit-rate
// panel).

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SettingsPage from './+page.svelte';
import { settings, type WorkspaceSettings } from '$lib/api/settings';
import { dispatch, type HitRate } from '$lib/api/dispatch';
import { update, type UpdateStatus } from '$lib/api/update';

vi.mock('$lib/api/settings', () => ({
	settings: { get: vi.fn(), update: vi.fn() }
}));

vi.mock('$lib/api/dispatch', () => ({
	dispatch: { hitRate: vi.fn() }
}));

vi.mock('$lib/api/update', () => ({
	update: { status: vi.fn(), apply: vi.fn() }
}));

const emptyHitRate: HitRate = {
	range: 'week',
	since: '2026-08-01T00:00:00Z',
	total_decisions: 0,
	hit_count: 0,
	fallback_count: 0,
	hit_rate: null,
	fallback_rate: null,
	fallback_warning: false,
	by_method: []
};

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

const upToDateStatus: UpdateStatus = {
	current_version: '0.1.0',
	latest_version: null,
	update_available: false,
	applicable: true
};

beforeEach(() => {
	// Tests that don't care about the hit-rate/update panels just need them
	// to settle without erroring; those that do override these per-test.
	vi.mocked(dispatch.hitRate).mockResolvedValue(emptyHitRate);
	vi.mocked(update.status).mockResolvedValue(upToDateStatus);
});

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

	it('shows the dispatcher hit rate once loaded', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(dispatch.hitRate).mockResolvedValue({
			...emptyHitRate,
			total_decisions: 20,
			hit_count: 18,
			fallback_count: 2,
			hit_rate: 0.9,
			fallback_rate: 0.1,
			fallback_warning: false,
			by_method: [
				{ method: 'deterministic', count: 18 },
				{ method: 'llm', count: 2 }
			]
		});

		render(SettingsPage);

		await expect.element(page.getByText('90%')).toBeInTheDocument();
		await expect.element(page.getByText('10%')).toBeInTheDocument();
		await expect.element(page.getByText('20 decisions')).toBeInTheDocument();
	});

	it('shows a cost warning banner when the fallback rate crosses 50%', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(dispatch.hitRate).mockResolvedValue({
			...emptyHitRate,
			total_decisions: 10,
			hit_count: 4,
			fallback_count: 6,
			hit_rate: 0.4,
			fallback_rate: 0.6,
			fallback_warning: true,
			by_method: [{ method: 'llm', count: 6 }]
		});

		render(SettingsPage);

		await expect
			.element(
				page.getByText('routing decisions this week fell back to the LLM router', { exact: false })
			)
			.toBeInTheDocument();
	});

	it('shows a message when there is no dispatcher activity yet', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(dispatch.hitRate).mockResolvedValue(emptyHitRate);

		render(SettingsPage);

		await expect
			.element(page.getByText('No dispatcher activity yet this week.'))
			.toBeInTheDocument();
	});

	it('shows an error when the hit-rate fetch fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(dispatch.hitRate).mockRejectedValueOnce(
			new Error('Failed to load dispatcher hit rate')
		);

		render(SettingsPage);

		await expect.element(page.getByText('Failed to load dispatcher hit rate')).toBeInTheDocument();
	});

	it('shows the current version and an up-to-date message', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);

		render(SettingsPage);

		await expect.element(page.getByText('0.1.0', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText("You're up to date.")).toBeInTheDocument();
	});

	it('offers an Update now button when a newer applicable version exists', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: true
		});

		render(SettingsPage);

		await expect
			.element(page.getByRole('button', { name: 'Update to v0.2.0' }))
			.toBeInTheDocument();
	});

	it('explains why updating is unavailable instead of showing a button', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: false
		});

		render(SettingsPage);

		await expect
			.element(page.getByText("Auto-update isn't available in this environment", { exact: false }))
			.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: /Update to/ })).not.toBeInTheDocument();
	});

	it('applies the update and shows a restarting message', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: true
		});
		vi.mocked(update.apply).mockResolvedValue({ status: 'restarting' });

		render(SettingsPage);
		await page.getByRole('button', { name: 'Update to v0.2.0' }).click();

		expect(update.apply).toHaveBeenCalled();
		await expect
			.element(page.getByText('Restarting to v0.2.0', { exact: false }))
			.toBeInTheDocument();
	});

	it('shows an error when applying the update fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: true
		});
		vi.mocked(update.apply).mockRejectedValueOnce(new Error('No update available.'));

		render(SettingsPage);
		await page.getByRole('button', { name: 'Update to v0.2.0' }).click();

		await expect.element(page.getByText('No update available.')).toBeInTheDocument();
	});

	it('shows an error when the update check fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(update.status).mockRejectedValueOnce(new Error('Failed to check for updates'));

		render(SettingsPage);

		await expect.element(page.getByText('Failed to check for updates')).toBeInTheDocument();
	});
});
