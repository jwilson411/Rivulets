// Browser-mode component test for the desktop channel sidebar: the
// filter must have a visible placeholder and an accessible name (#415).

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
	archived: false
};

describe('ContextPanel.svelte', () => {
	it('names the channel filter and hides channels that do not match', async () => {
		vi.mocked(channels.list).mockResolvedValue([testChannel]);

		render(ContextPanel);

		const search = page.getByRole('searchbox', { name: 'Filter channels', includeHidden: true });
		await expect.element(search).toBeInTheDocument();
		await expect.element(search).toHaveAttribute('placeholder', 'Filter channels');
		await expect.element(page.getByText('test-channel')).toBeInTheDocument();

		await search.fill('nope');
		await expect.element(page.getByText('test-channel')).not.toBeInTheDocument();
	});
});
