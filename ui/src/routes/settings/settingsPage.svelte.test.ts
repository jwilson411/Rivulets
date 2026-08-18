// Browser-mode component test for Settings (06-screens.md → Settings,
// mockup 1l): five tabs — Safety, Spend, Files, Integrations, Updates
// & backups — in plain language, with guests seeing spend status only
// (#351).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SettingsPage from './+page.svelte';
import { settings, type WorkspaceSettings } from '$lib/api/settings';
import { dispatch } from '$lib/api/dispatch';
import { update } from '$lib/api/update';
import { backups, type Backup } from '$lib/api/backups';
import { providers } from '$lib/api/providers';
import { integrations } from '$lib/api/integrations';
import { budgets, type BudgetStatus } from '$lib/api/budgets';
import { agents } from '$lib/api/agents';
import { teams } from '$lib/api/teams';

const authState = vi.hoisted(() => ({ grant: 'owner' }));
const routeState = vi.hoisted(() => ({ search: '' }));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return new URL('http://localhost/settings' + routeState.search);
		}
	}
}));

vi.mock('$lib/api/settings', () => ({
	settings: { get: vi.fn(), update: vi.fn(), listDirectories: vi.fn(), createDirectory: vi.fn() }
}));
vi.mock('$lib/api/dispatch', () => ({ dispatch: { hitRate: vi.fn() } }));
vi.mock('$lib/api/update', () => ({ update: { status: vi.fn(), apply: vi.fn() } }));
vi.mock('$lib/api/backups', () => ({
	backups: { list: vi.fn(), create: vi.fn(), restore: vi.fn() }
}));
vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn() } }));
vi.mock('$lib/api/integrations', () => ({
	integrations: {
		list: vi.fn(),
		googleOAuthApp: vi.fn(),
		saveGoogleOAuthApp: vi.fn(),
		connectGoogle: vi.fn(),
		disconnect: vi.fn()
	}
}));
vi.mock('$lib/api/budgets', () => ({
	budgets: { list: vi.fn(), create: vi.fn(), remove: vi.fn(), override: vi.fn() }
}));
vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
	routeState.search = '';
});

const defaults: WorkspaceSettings = {
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
	'ui.port': 8484,
	'tools.working_directory': null
};

const workspaceCap: BudgetStatus = {
	id: 'cap-1',
	scope_type: 'workspace',
	agent_id: null,
	team_id: null,
	period: 'day',
	limit_usd: 10,
	action: 'hard_stop',
	enabled: true,
	period_start: '2026-01-01T00:00:00Z',
	spend_usd: 10.4,
	unpriced_run_count: 0,
	breached: true,
	blocked: true,
	override_active: false
};

function seed() {
	vi.mocked(settings.get).mockResolvedValue(defaults);
	vi.mocked(dispatch.hitRate).mockResolvedValue({
		range: 'week',
		since: '2026-01-01T00:00:00Z',
		total_decisions: 20,
		hit_count: 18,
		fallback_count: 2,
		hit_rate: 0.9,
		fallback_rate: 0.1,
		fallback_warning: false,
		by_method: []
	});
	vi.mocked(update.status).mockResolvedValue({
		current_version: '0.5.0',
		latest_version: '0.5.0',
		update_available: false,
		applicable: false
	});
	vi.mocked(backups.list).mockResolvedValue([]);
	vi.mocked(providers.list).mockResolvedValue([]);
	vi.mocked(integrations.list).mockResolvedValue([]);
	vi.mocked(integrations.googleOAuthApp).mockResolvedValue({
		provider: 'google',
		client_id: '',
		has_client_secret: false,
		redirect_uri: 'http://127.0.0.1:8484/api/v1/integrations/google/callback'
	});
	vi.mocked(budgets.list).mockResolvedValue([]);
	vi.mocked(agents.list).mockResolvedValue([]);
	vi.mocked(teams.list).mockResolvedValue([]);
}

describe('settings/+page.svelte', () => {
	it('leads with the Safety tab and its plain-language rows', async () => {
		seed();

		render(SettingsPage);

		await expect.element(page.getByText('How conversations stop')).toBeInTheDocument();
		await expect.element(page.getByText('Max replies in a row')).toBeInTheDocument();
		await expect.element(page.getByText('Same two agents looping')).toBeInTheDocument();
		await expect.element(page.getByText('Pause after this much quiet')).toBeInTheDocument();
		await expect.element(page.getByLabelText('Max replies in a row')).toHaveValue(10);
	});

	it('saves only the safety fields that changed', async () => {
		seed();
		vi.mocked(settings.update).mockResolvedValueOnce({ ...defaults, 'guard.turn_limit': 12 });

		render(SettingsPage);
		await page.getByLabelText('Max replies in a row').fill('12');
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(settings.update).toHaveBeenCalledWith({ 'guard.turn_limit': 12 });
		await expect.element(page.getByText('Saved.')).toBeInTheDocument();
	});

	it('does not call settings.update when nothing changed', async () => {
		seed();

		render(SettingsPage);
		await expect.element(page.getByText('Max replies in a row')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(settings.update).not.toHaveBeenCalled();
	});

	it('lists spend caps with plain language and lifts a blocked one', async () => {
		seed();
		vi.mocked(budgets.list).mockResolvedValue([workspaceCap]);
		vi.mocked(budgets.override).mockResolvedValueOnce(workspaceCap);

		render(SettingsPage);
		await page.getByRole('button', { name: 'Spend' }).click();

		await expect.element(page.getByText('Whole workspace')).toBeInTheDocument();
		await expect.element(page.getByText('Stops when hit · per day')).toBeInTheDocument();
		await expect.element(page.getByText('$10.40 of $10.00')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Lift for now' }).click();
		expect(budgets.override).toHaveBeenCalledWith('cap-1');
	});

	it('adds a cap from the sheet with the stop-or-notify choice', async () => {
		seed();
		vi.mocked(budgets.create).mockResolvedValueOnce(workspaceCap);

		render(SettingsPage);
		await page.getByRole('button', { name: 'Spend' }).click();
		await page.getByRole('button', { name: 'Add a cap' }).click();

		await page.getByText('Stop when the cap is hit').click();
		await page.getByRole('button', { name: 'Add cap' }).click();

		expect(budgets.create).toHaveBeenCalledWith({
			scope_type: 'workspace',
			agent_id: null,
			team_id: null,
			period: 'day',
			limit_usd: 10,
			action: 'hard_stop'
		});
	});

	it('shows guests spend status only — no owner tabs, no owner-only GETs (#351)', async () => {
		authState.grant = 'invite';
		vi.mocked(budgets.list).mockResolvedValue([workspaceCap]);
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(SettingsPage);

		await expect.element(page.getByText('Whole workspace')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Safety' })).not.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Integrations' }))
			.not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Add a cap' })).not.toBeInTheDocument();
		expect(settings.get).not.toHaveBeenCalled();
		expect(update.status).not.toHaveBeenCalled();
		expect(backups.list).not.toHaveBeenCalled();
		expect(integrations.list).not.toHaveBeenCalled();
	});

	it('opens the Integrations tab from ?tab=integrations (#471)', async () => {
		seed();
		routeState.search = '?tab=integrations';

		render(SettingsPage);

		await expect
			.element(page.getByText('Connect a Google account so assigned agents', { exact: false }))
			.toBeInTheDocument();
		await expect
			.element(page.getByText('integrations:google:write', { exact: false }))
			.toBeInTheDocument();
		await expect.element(page.getByText('How conversations stop')).not.toBeInTheDocument();
	});

	it('lets the owner save a Google OAuth client and connect an account', async () => {
		seed();
		vi.mocked(integrations.saveGoogleOAuthApp).mockResolvedValue({
			provider: 'google',
			client_id: 'client-123',
			has_client_secret: false,
			redirect_uri: 'http://127.0.0.1:8484/api/v1/integrations/google/callback'
		});

		render(SettingsPage);
		await page.getByRole('button', { name: 'Integrations' }).click();

		await expect
			.element(page.getByText('Connect a Google account so assigned agents', { exact: false }))
			.toBeInTheDocument();
		await page.getByLabelText('Google OAuth client ID').fill('client-123');
		await page.getByRole('button', { name: 'Save client' }).click();
		expect(integrations.saveGoogleOAuthApp).toHaveBeenCalledWith({ client_id: 'client-123' });
		await expect
			.element(page.getByRole('button', { name: 'Connect Google account' }))
			.toBeInTheDocument();
	});

	it('lets the owner pick a project folder for agents', async () => {
		seed();
		vi.mocked(settings.listDirectories).mockResolvedValue({
			path: '/Users/ada/src',
			parent: '/Users/ada',
			entries: [{ name: 'rivulets', path: '/Users/ada/src/rivulets' }]
		});
		vi.mocked(settings.update).mockResolvedValueOnce({
			...defaults,
			'tools.working_directory': '/Users/ada/src'
		});

		render(SettingsPage);
		await page.getByRole('button', { name: 'Files' }).click();

		await expect.element(page.getByText('Default project folder')).toBeInTheDocument();
		await expect
			.element(page.getByText('Using the built-in sandbox until you pick a folder.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Choose folder' }).click();
		await expect.element(page.getByText('rivulets')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Use this folder' }).click();

		expect(settings.update).toHaveBeenCalledWith({
			'tools.working_directory': '/Users/ada/src'
		});
	});

	it('can clear the project folder back to the built-in sandbox', async () => {
		seed();
		vi.mocked(settings.get).mockResolvedValue({
			...defaults,
			'tools.working_directory': '/Users/ada/src/app'
		});
		vi.mocked(settings.update).mockResolvedValueOnce(defaults);

		render(SettingsPage);
		await page.getByRole('button', { name: 'Files' }).click();

		await expect.element(page.getByText('/Users/ada/src/app')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Use built-in sandbox' }).click();

		expect(settings.update).toHaveBeenCalledWith({ 'tools.working_directory': null });
	});

	it('saves the file-copy toggles the moment they flip', async () => {
		seed();
		vi.mocked(settings.update).mockResolvedValueOnce({ ...defaults, 'sync.eager_files_wan': true });

		render(SettingsPage);
		await page.getByRole('button', { name: 'Files' }).click();

		await page.getByRole('switch', { name: 'Copy files across the internet' }).click();

		expect(settings.update).toHaveBeenCalledWith({ 'sync.eager_files_wan': true });
	});

	it('shows the version and offers the update when one applies', async () => {
		seed();
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.5.0',
			latest_version: '0.6.0',
			update_available: true,
			applicable: true
		});
		vi.mocked(update.apply).mockResolvedValueOnce({ status: 'restarting' });

		render(SettingsPage);
		await page.getByRole('button', { name: 'Updates & backups' }).click();

		await page.getByRole('button', { name: 'Update to 0.6.0' }).click();

		expect(update.apply).toHaveBeenCalled();
		await expect
			.element(page.getByText('Restarting into 0.6.0', { exact: false }))
			.toBeInTheDocument();
	});

	it('takes a manual backup and lists existing ones', async () => {
		seed();
		const backup: Backup = {
			filename: 'backup-2026-01-01.tar',
			kind: 'daily',
			size_bytes: 2048,
			created_at: new Date().toISOString()
		};
		vi.mocked(backups.list).mockResolvedValue([backup]);
		vi.mocked(backups.create).mockResolvedValueOnce(backup);

		render(SettingsPage);
		await page.getByRole('button', { name: 'Updates & backups' }).click();

		await expect.element(page.getByText('backup-2026-01-01.tar')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Back up now' }).click();

		expect(backups.create).toHaveBeenCalled();
	});

	it('requires typing the backup name before a restore can run', async () => {
		seed();
		const backup: Backup = {
			filename: 'backup-2026-01-01.tar',
			kind: 'manual',
			size_bytes: 2048,
			created_at: new Date().toISOString()
		};
		vi.mocked(backups.list).mockResolvedValue([backup]);
		vi.mocked(backups.restore).mockResolvedValueOnce(undefined);

		render(SettingsPage);
		await page.getByRole('button', { name: 'Updates & backups' }).click();
		await page.getByRole('button', { name: 'Restore', exact: true }).click();

		const confirm = page.getByRole('button', { name: 'Restore', exact: true }).last();
		await expect.element(confirm).toBeDisabled();

		await page.getByLabelText('Backup name').fill('backup-2026-01-01.tar');
		await expect.element(confirm).toBeEnabled();
		await confirm.click();

		expect(backups.restore).toHaveBeenCalledWith('backup-2026-01-01.tar');
	});
});
