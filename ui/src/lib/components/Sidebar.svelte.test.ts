// Demonstrates the same browser-mode pattern as LoginForm.svelte.test.ts
// for a route-adjacent component -- Sidebar (like every route under
// src/routes/) depends on SvelteKit's $app/state and $app/paths, which
// need mocking too. Most of the app's components are this shape, so this
// is the more representative example to follow for future tests, not
// LoginForm's simpler case.

import { page as browserPage } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import Sidebar from './Sidebar.svelte';
import { approvals } from '$lib/api/approvals';
import { channels } from '$lib/api/channels';
import { agents } from '$lib/api/agents';
import { teams } from '$lib/api/teams';
import { providers } from '$lib/api/providers';
import { mcpServers } from '$lib/api/mcpServers';
import { tools } from '$lib/api/tools';
import { knowledgeBases } from '$lib/api/knowledgeBases';
import { workflows } from '$lib/api/workflows';
import { evals } from '$lib/api/evals';
import { sync } from '$lib/api/sync';
import { update } from '$lib/api/update';
import { theme } from '$lib/theme.svelte';

const routeState = vi.hoisted(() => ({ pathname: '/agents' }));
const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$app/state', () => ({
	page: {
		get url() {
			return new URL('http://localhost' + routeState.pathname);
		}
	}
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		displayName: 'Test User',
		get grant() {
			return authState.grant;
		},
		logout: vi.fn(),
		clearIdentity: vi.fn()
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));

vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));
vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn() } }));
vi.mock('$lib/api/mcpServers', () => ({ mcpServers: { list: vi.fn() } }));
vi.mock('$lib/api/tools', () => ({ tools: { list: vi.fn() } }));
vi.mock('$lib/api/knowledgeBases', () => ({ knowledgeBases: { list: vi.fn() } }));
vi.mock('$lib/api/workflows', () => ({ workflows: { list: vi.fn() } }));
vi.mock('$lib/api/evals', () => ({ evals: { listSuites: vi.fn() } }));
vi.mock('$lib/api/sync', () => ({ sync: { status: vi.fn() } }));
vi.mock('$lib/api/update', () => ({ update: { status: vi.fn() } }));
vi.mock('$lib/api/approvals', () => ({ approvals: { list: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
	theme.set('system');
	routeState.pathname = '/agents';
	authState.grant = 'owner';
	localStorage.clear();
});

describe('Sidebar.svelte', () => {
	it('lists channels loaded on mount', async () => {
		vi.mocked(channels.list).mockResolvedValueOnce([
			{
				id: 'chan-1',
				name: 'general',
				description: null,
				team_id: null,
				position: 0,
				archived: false
			}
		]);

		render(Sidebar);

		await expect.element(browserPage.getByText('#general')).toBeInTheDocument();
	});

	it('shows an error when loading channels fails', async () => {
		vi.mocked(channels.list).mockRejectedValueOnce(new Error('Failed to load channels'));

		render(Sidebar);

		await expect.element(browserPage.getByText('Failed to load channels')).toBeInTheDocument();
	});

	it('surfaces a server-rejected channel name instead of failing silently', async () => {
		// The regression this covers: handleCreateChannel used to have no
		// try/catch at all, so a validation failure became an unhandled
		// promise rejection with no on-screen feedback.
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(channels.create).mockRejectedValueOnce(new Error('name must be 3-80 chars'));

		render(Sidebar);
		await browserPage.getByPlaceholder('new-channel').fill('ab');
		await browserPage.getByRole('button', { name: 'Add' }).click();

		await expect.element(browserPage.getByText('name must be 3-80 chars')).toBeInTheDocument();
	});

	it('clears the input and refreshes the list after a successful create', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(channels.create).mockResolvedValueOnce({
			id: 'chan-2',
			name: 'new-chan',
			description: null,
			team_id: null,
			position: 0,
			archived: false
		});

		render(Sidebar);
		const input = browserPage.getByPlaceholder('new-channel');
		await input.fill('new-chan');
		await browserPage.getByRole('button', { name: 'Add' }).click();

		expect(channels.create).toHaveBeenCalledWith('new-chan');
		await expect.element(input).toHaveValue('');
	});

	it('switches the theme and marks the chosen option pressed', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		const darkButton = browserPage.getByTitle('Dark');
		await darkButton.click();

		expect(theme.preference).toBe('dark');
		expect(document.documentElement.dataset.theme).toBe('dark');
		await expect.element(darkButton).toHaveAttribute('aria-pressed', 'true');
		await expect.element(browserPage.getByTitle('System')).toHaveAttribute('aria-pressed', 'false');
	});

	it('auto-expands the workspace links when landing on a workspace route', async () => {
		routeState.pathname = '/agents';
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).toBeInTheDocument();
	});

	it('collapses the workspace links by default on a non-workspace route', async () => {
		routeState.pathname = '/channels/chan-1';
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect
			.element(browserPage.getByRole('button', { name: 'Workspace' }))
			.toBeInTheDocument();
		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).not.toBeInTheDocument();
	});

	it('toggles the workspace links open and closed, persisting the choice', async () => {
		routeState.pathname = '/channels/chan-1';
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);
		const toggle = browserPage.getByRole('button', { name: 'Workspace' });

		await toggle.click();
		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).toBeInTheDocument();
		expect(localStorage.getItem('rivulets-sidebar-workspace-expanded')).toBe('true');

		await toggle.click();
		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).not.toBeInTheDocument();
		expect(localStorage.getItem('rivulets-sidebar-workspace-expanded')).toBe('false');
	});

	it('restores a collapsed workspace section from a stored preference on a non-workspace route', async () => {
		routeState.pathname = '/channels/chan-1';
		localStorage.setItem('rivulets-sidebar-workspace-expanded', 'false');
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).not.toBeInTheDocument();
	});

	it('restores an expanded workspace section from a stored preference', async () => {
		routeState.pathname = '/channels/chan-1';
		localStorage.setItem('rivulets-sidebar-workspace-expanded', 'true');
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Agents/ })).toBeInTheDocument();
	});

	it('reveals the workspace section on a workspace route even if a stored preference says collapsed', async () => {
		routeState.pathname = '/teams';
		localStorage.setItem('rivulets-sidebar-workspace-expanded', 'false');
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Teams/ })).toBeInTheDocument();
	});

	it('shows the Workflows link under Workspace', async () => {
		routeState.pathname = '/agents';
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect
			.element(browserPage.getByRole('link', { name: /Workflows/ }))
			.toHaveAttribute('href', '/workflows');
	});

	it('links the ambient sync status readout to the sync page', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect
			.element(browserPage.getByRole('link', { name: /checking sync…/ }))
			.toHaveAttribute('href', '/sync');
	});

	function mockWorkspaceCounts() {
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(providers.list).mockResolvedValue([]);
		vi.mocked(mcpServers.list).mockResolvedValue([]);
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(knowledgeBases.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);
		vi.mocked(evals.listSuites).mockResolvedValue([]);
		vi.mocked(sync.status).mockResolvedValue({
			running: false,
			node_id: null,
			peers: [],
			pending_changes: 0
		});
		vi.mocked(approvals.list).mockResolvedValue([]);
	}

	it('shows an update badge next to Settings when one is available', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		mockWorkspaceCounts();
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: true
		});

		render(Sidebar);

		await expect.element(browserPage.getByTitle('Update available')).toBeInTheDocument();
	});

	it('shows no update badge when already up to date', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		mockWorkspaceCounts();
		vi.mocked(update.status).mockResolvedValue({
			current_version: '0.1.0',
			latest_version: null,
			update_available: false,
			applicable: true
		});

		render(Sidebar);
		await expect.element(browserPage.getByRole('link', { name: /Settings/ })).toBeInTheDocument();

		await expect.element(browserPage.getByTitle('Update available')).not.toBeInTheDocument();
	});

	it('shows a badge with the pending approval count', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		mockWorkspaceCounts();
		vi.mocked(approvals.list).mockResolvedValue([
			{
				id: 'a1',
				source_type: 'budget',
				schedule_id: null,
				budget_cap_id: 'cap-1',
				agent_id: null,
				title: 'Budget exceeded',
				detail: 'detail',
				status: 'pending',
				resolved_by: null,
				resolved_at: null,
				created_at: '2026-01-01T00:00:00Z'
			},
			{
				id: 'a2',
				source_type: 'budget',
				schedule_id: null,
				budget_cap_id: 'cap-2',
				agent_id: null,
				title: 'Already resolved',
				detail: 'detail',
				status: 'approved',
				resolved_by: 'human-1',
				resolved_at: '2026-01-02T00:00:00Z',
				created_at: '2026-01-01T00:00:00Z'
			}
		]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Approvals/ })).toBeInTheDocument();
		// Only the pending row counts toward the badge, not the resolved one.
		await expect.element(browserPage.getByText('1')).toBeInTheDocument();
	});

	it('shows the Invites link for an owner session', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByRole('link', { name: /Invites/ })).toBeInTheDocument();
	});

	it('hides the Invites link for a non-owner (invite-grant) session', async () => {
		authState.grant = 'invite';
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect
			.element(browserPage.getByRole('link', { name: /Invites/ }))
			.not.toBeInTheDocument();
	});

	it('shows the claimed identity and clears it via the switch control for an owner session', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect.element(browserPage.getByText(/Test User/)).toBeInTheDocument();
		await browserPage.getByText('switch').click();

		const { auth } = await import('$lib/api/auth.svelte');
		expect(auth.clearIdentity).toHaveBeenCalled();
	});
});
