import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ChannelsPage from './+page.svelte';
import { channels, type Channel } from '$lib/api/channels';
import { teams } from '$lib/api/teams';
import { goto } from '$app/navigation';

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

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn() }
}));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
});

const general: Channel = {
	id: 'chan-1',
	name: 'general',
	description: 'Default room',
	team_id: null,
	position: 0,
	archived: false,
	working_directory: null,
	effective_working_directory: null
};

const archived: Channel = {
	...general,
	id: 'chan-old',
	name: 'old-room',
	description: null,
	archived: true
};

describe('channels/+page.svelte', () => {
	it('lists active channels and hides archived ones', async () => {
		vi.mocked(channels.list).mockResolvedValue([general, archived]);

		render(ChannelsPage);

		await expect.element(page.getByText('general')).toBeInTheDocument();
		await expect.element(page.getByText('Default room')).toBeInTheDocument();
		await expect.element(page.getByText('old-room')).not.toBeInTheDocument();
	});

	it('shows archived channels behind the Archived chip', async () => {
		vi.mocked(channels.list).mockResolvedValue([general, archived]);

		render(ChannelsPage);
		await expect.element(page.getByText('general')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Archived' }).click();

		await expect.element(page.getByText('old-room')).toBeInTheDocument();
		await expect.element(page.getByText('general')).not.toBeInTheDocument();
	});

	it('opens the create sheet and navigates after create', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([
			{ id: 'team-starter', name: 'Starter Team', description: null }
		]);
		vi.mocked(channels.create).mockResolvedValue(general);

		render(ChannelsPage);
		await expect.element(page.getByText('No channels yet.')).toBeInTheDocument();

		await page.getByRole('button', { name: 'New channel' }).click();
		await page.getByLabelText('Name').fill('general');
		await page.getByRole('button', { name: 'Create channel' }).click();

		await expect.poll(() => vi.mocked(channels.create).mock.calls.length).toBe(1);
		expect(goto).toHaveBeenCalledWith('/channels/chan-1');
	});

	it('shows a retryable error when the list fails', async () => {
		vi.mocked(channels.list).mockRejectedValueOnce(new Error('boom'));

		render(ChannelsPage);

		await expect.element(page.getByText("Couldn't load channels.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
