// Browser-mode component test for the desktop channel sidebar: Search /
// jump opens the shared palette (#416). The local channel filter that
// #415 named is gone — jump-to-channel lives in the palette now.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ContextPanel from './ContextPanel.svelte';
import { channels, type Channel } from '$lib/api/channels';

const routeState = vi.hoisted(() => ({ pathname: '/' }));

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

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: { grant: 'owner' }
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));
vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
	routeState.pathname = '/';
});

const testChannel: Channel = {
	id: 'ch-1',
	name: 'test-channel',
	description: null,
	team_id: null,
	position: 0,
	archived: false,
	working_directory: null,
	effective_working_directory: null
};

describe('ContextPanel.svelte', () => {
	it('exposes Search / jump and still lists channels (#416)', async () => {
		vi.mocked(channels.list).mockResolvedValue([testChannel]);
		const onOpenPalette = vi.fn();

		render(ContextPanel, { onOpenPalette });

		// The aside is `lg:flex` / `hidden` at the default test viewport,
		// so query with includeHidden — click-to-open is covered on the
		// layout through More, which is always reachable.
		await expect
			.element(page.getByRole('button', { name: 'Search / jump', includeHidden: true }))
			.toBeInTheDocument();
		await expect.element(page.getByText('test-channel')).toBeInTheDocument();
		expect(onOpenPalette).not.toHaveBeenCalled();
	});

	it('shows the People section nav on /agents and hides owner-only items for guests', async () => {
		routeState.pathname = '/agents';
		const { auth } = await import('$lib/api/auth.svelte');
		(auth as { grant: string }).grant = 'invite';

		render(ContextPanel, { onOpenPalette: vi.fn() });

		await expect.element(page.getByLabelText('People')).toBeInTheDocument();
		await expect.element(page.getByText('Agents')).toBeInTheDocument();
		await expect.element(page.getByText('Providers')).not.toBeInTheDocument();
	});

	it('shows a load error when the channel list fails', async () => {
		vi.mocked(channels.list).mockRejectedValue(new Error('boom'));

		render(ContextPanel, { onOpenPalette: vi.fn() });

		await expect.element(page.getByText("Couldn't load channels.")).toBeInTheDocument();
	});

	it('opens the new-channel sheet from the chat-area sidebar', async () => {
		vi.mocked(channels.list).mockResolvedValue([testChannel]);
		const { teams } = await import('$lib/api/teams');
		vi.mocked(teams.list).mockResolvedValue([]);

		render(ContextPanel, { onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'New channel', includeHidden: true }).click();
		await expect.element(page.getByRole('heading', { name: 'New channel' })).toBeInTheDocument();
	});
});
