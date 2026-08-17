// Browser-mode component test for Home (06-screens.md → Home): the
// first-value setup cards while setup is incomplete, then the inbox of
// recent conversations with a Stream Bar that asks which channel to post
// into.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import HomePage from './+page.svelte';
import { channels, type Channel } from '$lib/api/channels';
import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
import { teams } from '$lib/api/teams';
import { providers, type Provider } from '$lib/api/providers';
import { agents, type Agent } from '$lib/api/agents';
import { goto } from '$app/navigation';

const authState = vi.hoisted(() => ({ grant: 'owner', displayName: 'Riley' }));

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

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		},
		get displayName() {
			return authState.displayName;
		}
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn(), create: vi.fn(), update: vi.fn() }
}));
vi.mock('$lib/api/rivulets', () => ({
	rivulets: {
		listForChannel: vi.fn(),
		listMessages: vi.fn(),
		create: vi.fn(),
		postMessage: vi.fn()
	}
}));
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn() } }));
vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn(), create: vi.fn() } }));
vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/files', () => ({ files: { upload: vi.fn() } }));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const general: Channel = {
	id: 'chan-1',
	name: 'general',
	description: 'Default room',
	team_id: 'team-1',
	position: 0,
	archived: false
};

const provider: Provider = {
	id: 'prov-1',
	provider: 'anthropic',
	label: 'Anthropic',
	base_url: null,
	is_default: true
};

const assistant: Agent = {
	id: 'agent-1',
	name: 'Assistant',
	description: 'Generalist',
	instructions: 'Help.',
	model: 'auto',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'agent-1'
};

const rivulet: Rivulet = {
	id: 'riv-1',
	channel_id: 'chan-1',
	title: 'Welcome to Rivulets. Ask the team anything.',
	status: 'active',
	created_by: 'human-1',
	created_at: '2026-01-01T00:00:00Z'
};

const messages: Message[] = [
	{
		id: 'msg-1',
		rivulet_id: 'riv-1',
		sender_type: 'human',
		sender_id: 'human-1',
		sender_name: 'Riley',
		content: 'Welcome to Rivulets. Ask the team anything.',
		content_type: 'text',
		created_at: '2026-01-01T00:00:00Z',
		attachments: [],
		model_used: null,
		tier: null,
		executed_node_id: null,
		served_model: null
	},
	{
		id: 'msg-2',
		rivulet_id: 'riv-1',
		sender_type: 'agent',
		sender_id: 'agent-1',
		sender_name: 'Assistant',
		content: "You're set up.",
		content_type: 'text',
		created_at: '2026-01-01T00:01:00Z',
		attachments: [],
		model_used: null,
		tier: null,
		executed_node_id: null,
		served_model: null
	}
];

function seedComplete() {
	vi.mocked(channels.list).mockResolvedValue([general]);
	vi.mocked(teams.list).mockResolvedValue([
		{ id: 'team-1', name: 'Starter Team', description: null }
	]);
	vi.mocked(providers.list).mockResolvedValue([provider]);
	vi.mocked(agents.list).mockResolvedValue([assistant]);
	vi.mocked(rivulets.listForChannel).mockResolvedValue([rivulet]);
	vi.mocked(rivulets.listMessages).mockResolvedValue(messages);
}

describe('routes/+page.svelte (Home)', () => {
	it('walks an owner through setup while no provider exists', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(providers.list).mockResolvedValue([]);

		render(HomePage);

		await expect
			.element(page.getByText('Three things before the team can answer'))
			.toBeInTheDocument();
		await expect.element(page.getByText('Add an API key')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Save key' })).toBeInTheDocument();
	});

	it('banners when a provider is connected but no agent is registered', async () => {
		seedComplete();
		vi.mocked(agents.list).mockResolvedValue([{ ...assistant, agentos_agent_id: null }]);

		render(HomePage);

		await expect
			.element(page.getByText(/Agents aren't ready to run on this node/))
			.toBeInTheDocument();
		await expect
			.element(page.getByText("Agents aren't ready to run — sign out and back in"))
			.toBeInTheDocument();
		await expect.element(page.getByText('Routes to Starter Team')).not.toBeInTheDocument();
	});

	it('shows recent conversations across channels once setup is complete', async () => {
		seedComplete();

		render(HomePage);

		await expect.element(page.getByText('Recent conversations')).toBeInTheDocument();
		await expect
			.element(page.getByText('Welcome to Rivulets. Ask the team anything.'))
			.toBeInTheDocument();
		await expect.element(page.getByText('#general', { exact: true }).first()).toBeInTheDocument();
		await expect.element(page.getByPlaceholder('Start a conversation…')).toBeInTheDocument();
		await expect
			.element(page.getByText('Starter Team answers when a rule or @mention matches'))
			.toBeInTheDocument();
	});

	it('shows the empty inbox with a way into #general when no conversations exist', async () => {
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);

		render(HomePage);

		await expect.element(page.getByText('No conversations yet.')).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Start one in #general' }))
			.toBeInTheDocument();
	});

	it('never shows setup cards to a guest — providers are owner-only', async () => {
		authState.grant = 'invite';
		seedComplete();
		vi.mocked(providers.list).mockRejectedValue(new Error('403'));

		render(HomePage);

		await expect.element(page.getByText('Recent conversations')).toBeInTheDocument();
		expect(providers.list).not.toHaveBeenCalled();
	});

	it('posting from Home creates a conversation in the picked channel and opens it', async () => {
		seedComplete();
		vi.mocked(rivulets.create).mockResolvedValue({ ...rivulet, id: 'riv-2' });

		render(HomePage);
		await expect.element(page.getByPlaceholder('Start a conversation…')).toBeInTheDocument();

		await page.getByPlaceholder('Start a conversation…').fill('Hello team');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.create).toHaveBeenCalledWith('chan-1', 'Hello team', []);
		expect(goto).toHaveBeenCalledWith('/channels/chan-1/rivulets/riv-2');
	});

	it('omits archived conversations from Recent conversations', async () => {
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([
			{ ...rivulet, id: 'riv-closed', status: 'closed', title: 'Old archived thread' }
		]);
		vi.mocked(rivulets.listMessages).mockResolvedValue([
			{ ...messages[0], rivulet_id: 'riv-closed', content: 'Old archived thread' }
		]);

		render(HomePage);

		await expect.element(page.getByText('No conversations yet.')).toBeInTheDocument();
		await expect.element(page.getByText('Old archived thread')).not.toBeInTheDocument();
	});

	it('continues the last conversation in the picked channel', async () => {
		seedComplete();
		vi.mocked(rivulets.postMessage).mockResolvedValueOnce(messages[0]);

		render(HomePage);
		await expect.element(page.getByText('Recent conversations')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Continue last' }).click();
		await page.getByPlaceholder('Reply to the last conversation…').fill('Following up');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-1', 'Following up', []);
		expect(rivulets.create).not.toHaveBeenCalled();
		expect(goto).toHaveBeenCalledWith('/channels/chan-1/rivulets/riv-1');
	});
});
