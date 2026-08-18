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
import { workflows, type Workflow } from '$lib/api/workflows';
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
vi.mock('$lib/api/teams', () => ({ teams: { list: vi.fn(), get: vi.fn() } }));
vi.mock('$lib/api/providers', () => ({ providers: { list: vi.fn(), create: vi.fn() } }));
vi.mock('$lib/api/agents', () => ({ agents: { list: vi.fn() } }));
vi.mock('$lib/api/workflows', () => ({ workflows: { list: vi.fn() } }));
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
	archived: false,
	working_directory: null,
	effective_working_directory: null
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
	created_at: '2026-01-01T00:00:00Z',
	working_directory: null,
	effective_working_directory: null
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
	vi.mocked(teams.get).mockResolvedValue({
		id: 'team-1',
		name: 'Starter Team',
		description: null,
		agent_ids: ['agent-1']
	});
	vi.mocked(workflows.list).mockResolvedValue([]);
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
		await expect.element(page.getByText(/Assistant is coordinating/)).toBeInTheDocument();
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

	it('scrolls the last conversation clear of the docked composer (#418)', async () => {
		const extras: Rivulet[] = Array.from({ length: 6 }, (_, i) => ({
			...rivulet,
			id: `riv-old-${i}`,
			title: i === 5 ? 'Yesterday standup notes' : `Older thread ${i + 1}`,
			created_at: `2025-12-${(30 - i).toString().padStart(2, '0')}T00:00:00Z`
		}));
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([rivulet, ...extras]);
		vi.mocked(rivulets.listMessages).mockImplementation(async (id) => {
			const extra = extras.find((r) => r.id === id);
			if (!extra) return messages;
			return [
				{
					...messages[0],
					id: `msg-${extra.id}`,
					rivulet_id: extra.id,
					content: extra.title ?? 'Conversation',
					created_at: extra.created_at
				}
			];
		});

		render(HomePage);

		const heading = page.getByRole('heading', { name: 'Home' });
		await expect.element(heading).toBeInTheDocument();
		const frame = heading.element().closest('div.relative');
		if (frame instanceof HTMLElement) {
			frame.style.width = '390px';
			frame.style.height = '780px';
		}

		const lastCard = page.getByRole('link', { name: /Yesterday standup notes/ });
		await expect.element(lastCard).toBeInTheDocument();
		const dock = page.getByRole('region', { name: 'Start a conversation' });
		await expect.element(dock).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Continue last' })).toBeInTheDocument();

		const scroller = lastCard.element().closest('.overflow-y-auto');
		if (scroller instanceof HTMLElement) {
			scroller.scrollTop = scroller.scrollHeight;
		}
		const cardBox = lastCard.element().getBoundingClientRect();
		const dockBox = dock.element().getBoundingClientRect();
		expect(cardBox.bottom).toBeLessThanOrEqual(dockBox.top + 1);
	});

	it('keeps channel chips on one scrolling row instead of wrapping (#418)', async () => {
		const extra: Channel = {
			...general,
			id: 'chan-2',
			name: 'My Channel',
			position: 1
		};
		const testChannel: Channel = { ...general, name: 'test-channel' };
		seedComplete();
		vi.mocked(channels.list).mockResolvedValue([testChannel, extra]);
		vi.mocked(rivulets.listForChannel).mockImplementation(async (id) =>
			id === testChannel.id ? [rivulet] : []
		);

		render(HomePage);

		const toolbar = page.getByRole('toolbar', { name: 'Post to channel' });
		await expect.element(toolbar).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: '#test-channel' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: '#My Channel' })).toBeInTheDocument();

		const el = toolbar.element();
		expect(el.className).toContain('flex-nowrap');
		expect(el.className).toContain('overflow-x-scroll');
		expect(getComputedStyle(el).flexWrap).toBe('nowrap');
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

	it('lists an older conversation first after a new reply (#469)', async () => {
		const older: Rivulet = {
			...rivulet,
			id: 'riv-older',
			title: 'Older thread we just replied to',
			created_at: '2026-01-01T00:00:00Z'
		};
		const newerIdle: Rivulet = {
			...rivulet,
			id: 'riv-newer',
			title: 'Newer idle thread',
			created_at: '2026-01-02T00:00:00Z'
		};
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([newerIdle, older]);
		vi.mocked(rivulets.listMessages).mockImplementation(async (id) => {
			if (id === older.id) {
				return [
					{
						...messages[0],
						id: 'msg-old-root',
						rivulet_id: older.id,
						content: older.title ?? 'Conversation',
						created_at: older.created_at
					},
					{
						...messages[0],
						id: 'msg-old-reply',
						rivulet_id: older.id,
						content: 'Just replied',
						created_at: '2026-01-03T12:00:00Z'
					}
				];
			}
			return [
				{
					...messages[0],
					id: 'msg-new-root',
					rivulet_id: newerIdle.id,
					content: newerIdle.title ?? 'Conversation',
					created_at: newerIdle.created_at
				}
			];
		});

		render(HomePage);

		const olderCard = page.getByRole('link', { name: /Older thread we just replied to/ });
		const newerCard = page.getByRole('link', { name: /Newer idle thread/ });
		await expect.element(olderCard).toBeInTheDocument();
		await expect.element(newerCard).toBeInTheDocument();
		const olderEl = olderCard.element();
		const newerEl = newerCard.element();
		expect(
			olderEl.compareDocumentPosition(newerEl) & Node.DOCUMENT_POSITION_FOLLOWING
		).toBeTruthy();
	});

	it('keeps an old reply in the top 8 instead of dropping it for newer starts (#469)', async () => {
		const extras: Rivulet[] = Array.from({ length: 8 }, (_, i) => ({
			...rivulet,
			id: `riv-idle-${i}`,
			title: `Idle thread ${i + 1}`,
			created_at: `2026-01-${(10 + i).toString().padStart(2, '0')}T00:00:00Z`
		}));
		const bumped: Rivulet = {
			...rivulet,
			id: 'riv-bumped',
			title: 'Bumped by a reply',
			created_at: '2026-01-01T00:00:00Z'
		};
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([...extras, bumped]);
		vi.mocked(rivulets.listMessages).mockImplementation(async (id) => {
			if (id === bumped.id) {
				return [
					{
						...messages[0],
						id: 'msg-bumped-root',
						rivulet_id: bumped.id,
						content: bumped.title ?? 'Conversation',
						created_at: bumped.created_at
					},
					{
						...messages[0],
						id: 'msg-bumped-reply',
						rivulet_id: bumped.id,
						content: 'Latest word',
						created_at: '2026-02-01T00:00:00Z'
					}
				];
			}
			const extra = extras.find((r) => r.id === id);
			return [
				{
					...messages[0],
					id: `msg-${id}`,
					rivulet_id: id,
					content: extra?.title ?? 'Conversation',
					created_at: extra?.created_at ?? '2026-01-10T00:00:00Z'
				}
			];
		});

		render(HomePage);

		await expect.element(page.getByRole('link', { name: /Bumped by a reply/ })).toBeInTheDocument();
		await expect.element(page.getByRole('link', { name: /Idle thread 1/ })).not.toBeInTheDocument();
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

	it('continues the conversation with the latest reply in the picked channel (#469)', async () => {
		const older: Rivulet = {
			...rivulet,
			id: 'riv-older',
			title: 'Older thread we just replied to',
			created_at: '2026-01-01T00:00:00Z'
		};
		const newerIdle: Rivulet = {
			...rivulet,
			id: 'riv-newer',
			title: 'Newer idle thread',
			created_at: '2026-01-02T00:00:00Z'
		};
		seedComplete();
		vi.mocked(rivulets.listForChannel).mockResolvedValue([newerIdle, older]);
		vi.mocked(rivulets.listMessages).mockImplementation(async (id) => {
			if (id === older.id) {
				return [
					{
						...messages[0],
						id: 'msg-old-root',
						rivulet_id: older.id,
						content: older.title ?? 'Conversation',
						created_at: older.created_at
					},
					{
						...messages[0],
						id: 'msg-old-reply',
						rivulet_id: older.id,
						content: 'Just replied',
						created_at: '2026-01-03T12:00:00Z'
					}
				];
			}
			return [
				{
					...messages[0],
					id: 'msg-new-root',
					rivulet_id: newerIdle.id,
					content: newerIdle.title ?? 'Conversation',
					created_at: newerIdle.created_at
				}
			];
		});
		vi.mocked(rivulets.postMessage).mockResolvedValueOnce(messages[0]);

		render(HomePage);
		await expect.element(page.getByText('Older thread we just replied to')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Continue last' }).click();
		await page.getByPlaceholder('Reply to the last conversation…').fill('Following up');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-older', 'Following up', []);
		expect(rivulets.create).not.toHaveBeenCalled();
		expect(goto).toHaveBeenCalledWith('/channels/chan-1/rivulets/riv-older');
	});

	it('opens an @mention picker for the selected channel team (#466)', async () => {
		seedComplete();

		render(HomePage);
		await expect.element(page.getByPlaceholder('Start a conversation…')).toBeInTheDocument();

		const input = page.getByPlaceholder('Start a conversation…');
		await input.fill('@');
		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();

		await page.getByRole('option', { name: /@Assistant/ }).click();
		await expect.element(input).toHaveValue('@Assistant ');
	});

	it('lists published slash workflows from the Home composer (#466)', async () => {
		const summarize: Workflow = {
			id: 'wf-1',
			name: 'summarize',
			description: 'Summarize the thread',
			published: true,
			on_failure_workflow_id: null,
			on_call_agent_id: null,
			created_at: '2026-01-01T00:00:00Z',
			updated_at: '2026-01-01T00:00:00Z'
		};
		seedComplete();
		vi.mocked(workflows.list).mockResolvedValue([summarize]);

		render(HomePage);
		await expect.element(page.getByPlaceholder('Start a conversation…')).toBeInTheDocument();

		await page.getByPlaceholder('Start a conversation…').fill('/');
		await expect.element(page.getByRole('menuitem', { name: /\/summarize/ })).toBeInTheDocument();
	});

	it('switches mention candidates when the Home channel chip changes (#466)', async () => {
		const researcher: Agent = {
			...assistant,
			id: 'agent-2',
			name: 'Researcher',
			description: 'Looks things up',
			agentos_agent_id: 'agent-2'
		};
		const other: Channel = {
			...general,
			id: 'chan-2',
			name: 'research',
			team_id: 'team-2',
			position: 1
		};
		seedComplete();
		vi.mocked(channels.list).mockResolvedValue([general, other]);
		vi.mocked(agents.list).mockResolvedValue([assistant, researcher]);
		vi.mocked(teams.list).mockResolvedValue([
			{ id: 'team-1', name: 'Starter Team', description: null },
			{ id: 'team-2', name: 'Research', description: null }
		]);
		vi.mocked(teams.get).mockImplementation(async (id: string) =>
			id === 'team-2'
				? { id: 'team-2', name: 'Research', description: null, agent_ids: ['agent-2'] }
				: { id: 'team-1', name: 'Starter Team', description: null, agent_ids: ['agent-1'] }
		);
		vi.mocked(rivulets.listForChannel).mockImplementation(async (id) =>
			id === general.id ? [rivulet] : []
		);

		render(HomePage);
		await expect.element(page.getByRole('button', { name: '#research' })).toBeInTheDocument();

		const input = page.getByPlaceholder('Start a conversation…');
		await input.fill('@');
		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();
		await expect.element(page.getByRole('option', { name: /@Researcher/ })).not.toBeInTheDocument();

		await page.getByRole('button', { name: '#research' }).click();
		await input.fill('@');
		await expect.element(page.getByRole('option', { name: /@Researcher/ })).toBeInTheDocument();
		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();
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
