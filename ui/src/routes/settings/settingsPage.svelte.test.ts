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
import { backups as backupsApi, type Backup } from '$lib/api/backups';
import { providers as providersApi, type Provider } from '$lib/api/providers';
import { budgets as budgetsApi, type BudgetStatus } from '$lib/api/budgets';
import { agents as agentsApi, type Agent } from '$lib/api/agents';
import { teams as teamsApi, type Team } from '$lib/api/teams';

vi.mock('$lib/api/settings', () => ({
	settings: { get: vi.fn(), update: vi.fn() }
}));

vi.mock('$lib/api/dispatch', () => ({
	dispatch: { hitRate: vi.fn() }
}));

// The Dispatcher section's ModelPicker is the only combobox in it — the
// budget-caps form (#97) elsewhere on the page added its own Scope/Period/
// On-breach selects, so an unscoped page.getByRole('combobox') now matches
// multiple elements.
async function dispatcherSection() {
	const heading = page.getByRole('heading', { name: 'Dispatcher' });
	await expect.element(heading).toBeInTheDocument();
	return page.elementLocator(heading.element().closest('section')!);
}

vi.mock('$lib/api/update', () => ({
	update: { status: vi.fn(), apply: vi.fn() }
}));

vi.mock('$lib/api/backups', () => ({
	backups: { list: vi.fn(), create: vi.fn(), restore: vi.fn() }
}));

async function backupsSection() {
	const heading = page.getByRole('heading', { name: 'Backups' });
	await expect.element(heading).toBeInTheDocument();
	return page.elementLocator(heading.element().closest('section')!);
}

vi.mock('$lib/api/providers', () => ({
	providers: { list: vi.fn() }
}));

vi.mock('$lib/api/budgets', () => ({
	budgets: { list: vi.fn(), create: vi.fn(), remove: vi.fn(), override: vi.fn() }
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn() }
}));

async function budgetsSection() {
	const heading = page.getByRole('heading', { name: 'Budgets' });
	await expect.element(heading).toBeInTheDocument();
	return page.elementLocator(heading.element().closest('section')!);
}

const workspaceCap: BudgetStatus = {
	id: 'cap-1',
	scope_type: 'workspace',
	agent_id: null,
	team_id: null,
	period: 'month',
	limit_usd: 100,
	action: 'alert',
	enabled: true,
	period_start: '2026-08-01T00:00:00Z',
	spend_usd: 40,
	unpriced_run_count: 0,
	breached: false,
	blocked: false,
	override_active: false
};

const blockedAgentCap: BudgetStatus = {
	id: 'cap-2',
	scope_type: 'agent',
	agent_id: 'agent-1',
	team_id: null,
	period: 'day',
	limit_usd: 10,
	action: 'hard_stop',
	enabled: true,
	period_start: '2026-08-10T00:00:00Z',
	spend_usd: 12,
	unpriced_run_count: 3,
	breached: true,
	blocked: true,
	override_active: false
};

const oneAgent: Agent = {
	id: 'agent-1',
	name: 'Support Bot',
	description: '',
	instructions: '',
	model: 'test:mock',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: null
};

const oneTeam: Team = { id: 'team-1', name: 'Support', description: null };

const anthropicProvider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'My Anthropic key',
	base_url: null,
	is_default: true
};

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
	// Tests that don't care about the hit-rate/update/providers panels just
	// need them to settle without erroring; those that do override these
	// per-test.
	vi.mocked(dispatch.hitRate).mockResolvedValue(emptyHitRate);
	vi.mocked(update.status).mockResolvedValue(upToDateStatus);
	vi.mocked(backupsApi.list).mockResolvedValue([]);
	vi.mocked(providersApi.list).mockResolvedValue([]);
	vi.mocked(budgetsApi.list).mockResolvedValue([]);
	vi.mocked(agentsApi.list).mockResolvedValue([]);
	vi.mocked(teamsApi.list).mockResolvedValue([]);
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
		const cycleThresholdInput = page.getByRole('spinbutton', { name: /Cycle threshold/ });
		await expect.element(cycleThresholdInput).toHaveValue(3);
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

	it('saves a changed cycle threshold', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(settings.update).mockResolvedValueOnce({
			...loadedSettings,
			'guard.cycle_threshold': 5
		});

		render(SettingsPage);
		const cycleThresholdInput = page.getByRole('spinbutton', { name: /Cycle threshold/ });
		await expect.element(cycleThresholdInput).toHaveValue(3);

		await cycleThresholdInput.fill('5');
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(settings.update).toHaveBeenCalledWith({ 'guard.cycle_threshold': 5 });
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

	it('offers a provider-model picker for the dispatcher override instead of a freeform text field', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(providersApi.list).mockResolvedValue([anthropicProvider]);

		render(SettingsPage);

		const select = (await dispatcherSection()).getByRole('combobox');
		await expect
			.element(select.getByRole('option', { name: /Claude Haiku 4\.5/ }))
			.toBeInTheDocument();
		await expect
			.element(page.getByRole('option', { name: 'Auto — picks cheap or capable per message' }))
			.not.toBeInTheDocument();
	});

	it('saves a picked dispatcher override as provider:model', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(providersApi.list).mockResolvedValue([anthropicProvider]);
		vi.mocked(settings.update).mockResolvedValueOnce({
			...loadedSettings,
			'dispatcher.model_override': 'anthropic:claude-haiku-4-5-20251001'
		});

		render(SettingsPage);
		const select = (await dispatcherSection()).getByRole('combobox');
		await expect
			.element(select.getByRole('option', { name: /Claude Haiku 4\.5/ }))
			.toBeInTheDocument();

		await select.selectOptions('anthropic:claude-haiku-4-5-20251001');
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(settings.update).toHaveBeenCalledWith({
			'dispatcher.model_override': 'anthropic:claude-haiku-4-5-20251001'
		});
	});

	it('seeds the picker from an existing provider:model override', async () => {
		vi.mocked(settings.get).mockResolvedValue({
			...loadedSettings,
			'dispatcher.model_override': 'anthropic:claude-haiku-4-5-20251001'
		});
		vi.mocked(providersApi.list).mockResolvedValue([anthropicProvider]);

		render(SettingsPage);

		const select = (await dispatcherSection()).getByRole('combobox');
		await expect.element(select).toBeInTheDocument();
		expect((select.element() as HTMLSelectElement).value).toBe(
			'anthropic:claude-haiku-4-5-20251001'
		);
	});

	it('shows an error when loading providers fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(providersApi.list).mockRejectedValueOnce(new Error('Failed to load providers'));

		render(SettingsPage);

		await expect.element(page.getByText('Failed to load providers')).toBeInTheDocument();
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

	it('shows the empty state when no backups exist', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);

		render(SettingsPage);

		const section = await backupsSection();
		await expect.element(section.getByText('No backups yet.')).toBeInTheDocument();
	});

	it('lists backups with kind, size, and age', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		const oneBackup: Backup = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 2048,
			created_at: new Date().toISOString()
		};
		vi.mocked(backupsApi.list).mockResolvedValue([oneBackup]);

		render(SettingsPage);

		const section = await backupsSection();
		await expect.element(section.getByText('manual-2026-01-01T000000Z.tar')).toBeInTheDocument();
		await expect
			.element(section.getByText('Manual · 2.0 KB', { exact: false }))
			.toBeInTheDocument();
	});

	it('shows an error when loading backups fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(backupsApi.list).mockRejectedValueOnce(new Error('Failed to load backups'));

		render(SettingsPage);

		const section = await backupsSection();
		await expect.element(section.getByText('Failed to load backups')).toBeInTheDocument();
	});

	it('creates a manual backup and refreshes the list', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		const created: Backup = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 1024,
			created_at: new Date().toISOString()
		};
		vi.mocked(backupsApi.create).mockResolvedValueOnce(created);
		vi.mocked(backupsApi.list).mockResolvedValueOnce([]).mockResolvedValueOnce([created]);

		render(SettingsPage);

		const section = await backupsSection();
		await section.getByRole('button', { name: 'Back up now' }).click();

		expect(backupsApi.create).toHaveBeenCalled();
		await expect.element(section.getByText('manual-2026-01-01T000000Z.tar')).toBeInTheDocument();
	});

	it('shows an error when creating a backup fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(backupsApi.create).mockRejectedValueOnce(new Error('Failed to create backup'));

		render(SettingsPage);

		const section = await backupsSection();
		await section.getByRole('button', { name: 'Back up now' }).click();

		await expect.element(section.getByText('Failed to create backup')).toBeInTheDocument();
	});

	it('requires the exact filename to be typed before confirming a restore', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		const oneBackup: Backup = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 1024,
			created_at: new Date().toISOString()
		};
		vi.mocked(backupsApi.list).mockResolvedValue([oneBackup]);

		render(SettingsPage);

		const section = await backupsSection();
		await section.getByRole('button', { name: 'Restore' }).click();

		const confirmButton = section.getByRole('button', { name: 'Confirm restore' });
		await expect.element(confirmButton).toBeDisabled();

		const input = section.getByLabelText('Filename');
		await input.fill('not-the-right-filename.tar');
		await expect.element(confirmButton).toBeDisabled();

		await input.fill(oneBackup.filename);
		await expect.element(confirmButton).not.toBeDisabled();

		await confirmButton.click();
		expect(backupsApi.restore).toHaveBeenCalledWith(oneBackup.filename);
		await expect
			.element(section.getByText('Restored. Reload the page', { exact: false }))
			.toBeInTheDocument();
	});

	it('cancels a restore without calling the API', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		const oneBackup: Backup = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 1024,
			created_at: new Date().toISOString()
		};
		vi.mocked(backupsApi.list).mockResolvedValue([oneBackup]);

		render(SettingsPage);

		const section = await backupsSection();
		await section.getByRole('button', { name: 'Restore' }).click();
		await section.getByRole('button', { name: 'Cancel' }).click();

		await expect.element(section.getByRole('button', { name: 'Restore' })).toBeInTheDocument();
		expect(backupsApi.restore).not.toHaveBeenCalled();
	});

	it('shows an error when restoring fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		const oneBackup: Backup = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 1024,
			created_at: new Date().toISOString()
		};
		vi.mocked(backupsApi.list).mockResolvedValue([oneBackup]);
		vi.mocked(backupsApi.restore).mockRejectedValueOnce(new Error('Failed to restore backup'));

		render(SettingsPage);

		const section = await backupsSection();
		await section.getByRole('button', { name: 'Restore' }).click();
		const input = section.getByLabelText('Filename');
		await input.fill(oneBackup.filename);
		await section.getByRole('button', { name: 'Confirm restore' }).click();

		await expect.element(section.getByText('Failed to restore backup')).toBeInTheDocument();
	});

	it('shows the empty state when no budget caps are configured', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);

		render(SettingsPage);

		const section = await budgetsSection();
		await expect.element(section.getByText('No budget caps configured yet.')).toBeInTheDocument();
	});

	it('lists budget caps with scope, spend, and unpriced-run counts', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockResolvedValue([workspaceCap, blockedAgentCap]);
		vi.mocked(agentsApi.list).mockResolvedValue([oneAgent]);

		render(SettingsPage);

		const section = await budgetsSection();
		const list = section.getByRole('list');
		await expect.element(list.getByText('Whole workspace')).toBeInTheDocument();
		await expect.element(list.getByText('Support Bot')).toBeInTheDocument();
		await expect.element(list.getByText('$40.00 / $100.00', { exact: false })).toBeInTheDocument();
		await expect
			.element(section.getByText('3 unpriced runs not counted', { exact: false }))
			.toBeInTheDocument();
	});

	it('shows an error when loading budget caps fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockRejectedValueOnce(new Error('Failed to load budget caps'));

		render(SettingsPage);

		const section = await budgetsSection();
		await expect.element(section.getByText('Failed to load budget caps')).toBeInTheDocument();
	});

	it('only shows an Override button for blocked caps', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockResolvedValue([workspaceCap, blockedAgentCap]);
		vi.mocked(agentsApi.list).mockResolvedValue([oneAgent]);

		render(SettingsPage);

		const section = await budgetsSection();
		await expect.element(section.getByRole('button', { name: 'Override' })).toBeInTheDocument();
		expect(section.getByRole('button', { name: 'Override' }).elements().length).toBe(1);
	});

	it('overrides a blocked budget cap and refreshes the list', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockResolvedValue([blockedAgentCap]);
		vi.mocked(agentsApi.list).mockResolvedValue([oneAgent]);
		vi.mocked(budgetsApi.override).mockResolvedValueOnce({
			...blockedAgentCap,
			blocked: false,
			override_active: true
		});

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByRole('button', { name: 'Override' }).click();

		expect(budgetsApi.override).toHaveBeenCalledWith('cap-2');
		await vi.waitFor(() => expect(budgetsApi.list).toHaveBeenCalledTimes(2));
	});

	it('deletes a budget cap and refreshes the list', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockResolvedValue([workspaceCap]);
		vi.mocked(budgetsApi.remove).mockResolvedValueOnce(undefined);

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByRole('button', { name: 'Delete' }).click();

		expect(budgetsApi.remove).toHaveBeenCalledWith('cap-1');
		await vi.waitFor(() => expect(budgetsApi.list).toHaveBeenCalledTimes(2));
	});

	it('shows an error when deleting a budget cap fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.list).mockResolvedValue([workspaceCap]);
		vi.mocked(budgetsApi.remove).mockRejectedValueOnce(new Error('Failed to delete budget cap'));

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByRole('button', { name: 'Delete' }).click();

		await expect.element(section.getByText('Failed to delete budget cap')).toBeInTheDocument();
	});

	it('creates a workspace-scoped budget cap from the form defaults', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.create).mockResolvedValueOnce({
			id: 'cap-3',
			scope_type: 'workspace',
			agent_id: null,
			team_id: null,
			period: 'day',
			limit_usd: 10,
			action: 'alert',
			enabled: true
		});

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByRole('button', { name: 'Add cap' }).click();

		expect(budgetsApi.create).toHaveBeenCalledWith({
			scope_type: 'workspace',
			agent_id: null,
			team_id: null,
			period: 'day',
			limit_usd: 10,
			action: 'alert'
		});
		await vi.waitFor(() => expect(budgetsApi.list).toHaveBeenCalledTimes(2));
	});

	it('scopes a new cap to a specific agent once one is picked', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(agentsApi.list).mockResolvedValue([oneAgent]);
		vi.mocked(budgetsApi.create).mockResolvedValueOnce({
			id: 'cap-4',
			scope_type: 'agent',
			agent_id: 'agent-1',
			team_id: null,
			period: 'day',
			limit_usd: 10,
			action: 'alert',
			enabled: true
		});

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByLabelText('Scope').selectOptions('agent');

		const addButton = section.getByRole('button', { name: 'Add cap' });
		await expect.element(addButton).toBeDisabled();

		// The accessible name of a label-wrapped <select> includes its
		// currently selected option text (e.g. "Agent Select an agentSupport
		// Bot"), so an exact match on just "Agent" still fails -- anchor on
		// the start instead to avoid matching the Scope select, whose name
		// also contains the substring "Agent" (one of its own options).
		await section.getByLabelText(/^Agent\b/).selectOptions('agent-1');
		await expect.element(addButton).not.toBeDisabled();
		await addButton.click();

		expect(budgetsApi.create).toHaveBeenCalledWith({
			scope_type: 'agent',
			agent_id: 'agent-1',
			team_id: null,
			period: 'day',
			limit_usd: 10,
			action: 'alert'
		});
	});

	it('scopes a new cap to a specific team once one is picked', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(teamsApi.list).mockResolvedValue([oneTeam]);
		vi.mocked(budgetsApi.create).mockResolvedValueOnce({
			id: 'cap-5',
			scope_type: 'team',
			agent_id: null,
			team_id: 'team-1',
			period: 'day',
			limit_usd: 10,
			action: 'alert',
			enabled: true
		});

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByLabelText('Scope').selectOptions('team');
		await section.getByLabelText(/^Team\b/).selectOptions('team-1');
		await section.getByRole('button', { name: 'Add cap' }).click();

		expect(budgetsApi.create).toHaveBeenCalledWith({
			scope_type: 'team',
			agent_id: null,
			team_id: 'team-1',
			period: 'day',
			limit_usd: 10,
			action: 'alert'
		});
	});

	it('shows an error when creating a budget cap fails', async () => {
		vi.mocked(settings.get).mockResolvedValue(loadedSettings);
		vi.mocked(budgetsApi.create).mockRejectedValueOnce(new Error('Failed to create budget cap'));

		render(SettingsPage);

		const section = await budgetsSection();
		await section.getByRole('button', { name: 'Add cap' }).click();

		await expect.element(section.getByText('Failed to create budget cap')).toBeInTheDocument();
	});
});
