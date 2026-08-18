import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SetupCards from './SetupCards.svelte';
import { providers } from '$lib/api/providers';
import { channels } from '$lib/api/channels';
import { teams } from '$lib/api/teams';
import { goto } from '$app/navigation';
import type { Channel } from '$lib/api/channels';
import type { Provider } from '$lib/api/providers';

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

vi.mock('$lib/api/providers', () => ({ providers: { create: vi.fn() } }));
vi.mock('$lib/api/channels', () => ({ channels: { create: vi.fn() } }));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
});

const provider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'Anthropic',
	base_url: null,
	is_default: true
};

const general: Channel = {
	id: 'chan-1',
	name: 'general',
	description: null,
	team_id: 'team-1',
	position: 0,
	archived: false,
	working_directory: null,
	effective_working_directory: null
};

describe('SetupCards.svelte', () => {
	it('saves an API key from the first card', async () => {
		vi.mocked(providers.create).mockResolvedValue(provider);
		const onProviderAdded = vi.fn();

		render(SetupCards, { providerList: [], channelList: [], onProviderAdded });

		await page.getByPlaceholder('sk-ant-…').fill('sk-ant-test');
		await page.getByRole('button', { name: 'Save key' }).click();

		await expect.poll(() => vi.mocked(providers.create).mock.calls.length).toBe(1);
		expect(providers.create).toHaveBeenCalledWith({
			provider: 'anthropic',
			label: 'Anthropic',
			api_key: 'sk-ant-test',
			base_url: undefined
		});
		expect(onProviderAdded).toHaveBeenCalledOnce();
	});

	it('shows a local address field for Ollama and uses a placeholder key', async () => {
		vi.mocked(providers.create).mockResolvedValue({
			...provider,
			provider: 'ollama',
			label: 'Ollama'
		});
		const onProviderAdded = vi.fn();

		render(SetupCards, { providerList: [], channelList: [], onProviderAdded });
		await page.getByRole('button', { name: 'Ollama' }).click();
		await page.getByPlaceholder('http://localhost:11434').fill('http://localhost:11434');
		await page.getByRole('button', { name: 'Save key' }).click();

		await expect.poll(() => vi.mocked(providers.create).mock.calls.length).toBe(1);
		expect(providers.create).toHaveBeenCalledWith({
			provider: 'ollama',
			label: 'Ollama',
			api_key: 'ollama',
			base_url: 'http://localhost:11434'
		});
	});

	it('opens #general when a provider exists but no channel does', async () => {
		vi.mocked(teams.list).mockResolvedValue([
			{ id: 'team-starter', name: 'Starter Team', description: null }
		]);
		vi.mocked(channels.create).mockResolvedValue(general);

		render(SetupCards, { providerList: [provider], channelList: [], onProviderAdded: vi.fn() });

		await page.getByRole('button', { name: 'Open #general' }).click();

		await expect.poll(() => vi.mocked(channels.create).mock.calls.length).toBe(1);
		expect(goto).toHaveBeenCalledWith('/channels/chan-1');
	});

	it('takes the user to an existing general channel on step 3', async () => {
		render(SetupCards, {
			providerList: [provider],
			channelList: [general],
			onProviderAdded: vi.fn()
		});

		await expect.element(page.getByText('Add an API key')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Take me to #general' }).click();

		expect(channels.create).not.toHaveBeenCalled();
		expect(goto).toHaveBeenCalledWith('/channels/chan-1');
	});

	it('shows the rejected-key message when save fails', async () => {
		vi.mocked(providers.create).mockRejectedValue(new Error('bad key'));

		render(SetupCards, { providerList: [], channelList: [], onProviderAdded: vi.fn() });
		await page.getByPlaceholder('sk-ant-…').fill('sk-bad');
		await page.getByRole('button', { name: 'Save key' }).click();

		await expect
			.element(page.getByText('That key was rejected. Check it and try again.'))
			.toBeInTheDocument();
	});
});
