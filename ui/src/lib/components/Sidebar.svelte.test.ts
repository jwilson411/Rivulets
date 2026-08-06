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

vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/agents') }
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
});
