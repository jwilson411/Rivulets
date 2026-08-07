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
import { channels } from '$lib/api/channels';
import { theme } from '$lib/theme.svelte';

const routeState = vi.hoisted(() => ({ pathname: '/agents' }));

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
	auth: { logout: vi.fn() }
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
	theme.set('system');
	routeState.pathname = '/agents';
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

	it('links the ambient sync status readout to the sync page', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);

		render(Sidebar);

		await expect
			.element(browserPage.getByRole('link', { name: /checking sync…/ }))
			.toHaveAttribute('href', '/sync');
	});
});
