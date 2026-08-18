import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import IconRail from './IconRail.svelte';
import { goto } from '$app/navigation';
import { writeLastChannel } from '$lib/lastChannel';
import { approvals } from '$lib/api/approvals';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		let out = path;
		if (params) {
			for (const [key, value] of Object.entries(params)) out = out.replace(`[${key}]`, value);
		}
		return out;
	}
}));

vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/') }
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: { displayName: 'Riley' }
}));

vi.mock('$lib/api/approvals', () => ({
	approvals: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
	localStorage.removeItem('rivulets-last-channel');
});

describe('IconRail.svelte', () => {
	it('opens the last channel from the Hash button', async () => {
		vi.mocked(approvals.list).mockResolvedValue([]);
		writeLastChannel('chan-last');

		render(IconRail, { onOpenMore: vi.fn(), onOpenAccount: vi.fn(), onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'Channels', includeHidden: true }).click();

		expect(goto).toHaveBeenCalledWith('/channels/chan-last');
	});

	it('falls back to the channel list when no last channel is stored', async () => {
		vi.mocked(approvals.list).mockResolvedValue([]);

		render(IconRail, { onOpenMore: vi.fn(), onOpenAccount: vi.fn(), onOpenPalette: vi.fn() });

		await page.getByRole('button', { name: 'Channels', includeHidden: true }).click();

		expect(goto).toHaveBeenCalledWith('/channels');
	});
});
