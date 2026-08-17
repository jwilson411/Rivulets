// Browser-mode component test for the Channel page (06-screens.md →
// Channel, mockup 1f): thread cards, the team chip menu, and the Stream
// Bar that CREATES a conversation (never a channel-root transcript).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ChannelPage from './+page.svelte';
import { channels, type Channel } from '$lib/api/channels';
import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
import { teams, type Team } from '$lib/api/teams';
import { agents, type Agent } from '$lib/api/agents';
import { files as filesApi } from '$lib/api/files';
import { workflows as workflowsApi } from '$lib/api/workflows';
import { runs, type RunTrace } from '$lib/api/runs';
import { goto } from '$app/navigation';

vi.mock('$app/state', () => ({
	page: { params: { id: 'chan-1' }, url: new URL('http://localhost/channels/chan-1') }
}));

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
	channels: { get: vi.fn(), update: vi.fn() }
}));

vi.mock('$lib/api/rivulets', () => ({
	rivulets: { listForChannel: vi.fn(), listMessages: vi.fn(), create: vi.fn() }
}));

vi.mock('$lib/api/teams', () => ({
	teams: { list: vi.fn(), get: vi.fn() }
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

vi.mock('$lib/api/workflows', () => ({
	workflows: { list: vi.fn() }
}));

vi.mock('$lib/api/runs', () => ({
	runs: { list: vi.fn() }
}));

vi.mock('$lib/api/files', () => ({
	files: { upload: vi.fn(), download: vi.fn() }
}));

const generalChannel: Channel = {
	id: 'chan-1',
	name: 'general',
	description: null,
	team_id: null,
	position: 0,
	archived: false
};

const supportTeam: Team = { id: 'team-1', name: 'Support', description: null };

const kickoffRivulet: Rivulet = {
	id: 'riv-1',
	channel_id: 'chan-1',
	title: null,
	status: 'active',
	created_by: 'user-1',
	created_at: new Date().toISOString()
};

const humanMessage: Message = {
	id: 'msg-1',
	rivulet_id: 'riv-1',
	sender_type: 'human',
	sender_id: null,
	sender_name: 'Justin',
	content: 'Kickoff message',
	content_type: 'text',
	created_at: new Date().toISOString(),
	attachments: [],
	model_used: null,
	tier: null,
	executed_node_id: null,
	served_model: null
};

const agentMessage: Message = {
	id: 'msg-2',
	rivulet_id: 'riv-1',
	sender_type: 'agent',
	sender_id: 'agent-1',
	sender_name: 'Researcher',
	content: 'On it',
	content_type: 'text',
	created_at: new Date().toISOString(),
	attachments: [],
	model_used: null,
	tier: null,
	executed_node_id: null,
	served_model: null
};

function seed(overrides?: { channel?: Channel; rivulets?: Rivulet[]; teams?: Team[] }) {
	vi.mocked(channels.get).mockResolvedValue(overrides?.channel ?? generalChannel);
	vi.mocked(rivulets.listForChannel).mockResolvedValue(overrides?.rivulets ?? []);
	vi.mocked(teams.list).mockResolvedValue(overrides?.teams ?? []);
	vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, agentMessage]);
	vi.mocked(workflowsApi.list).mockResolvedValue([]);
	vi.mocked(runs.list).mockResolvedValue([]);
}

afterEach(() => {
	vi.clearAllMocks();
});

describe('channels/[id]/+page.svelte', () => {
	it('loads the channel by page.params.id and shows a thread card', async () => {
		seed({ rivulets: [kickoffRivulet] });

		render(ChannelPage);

		expect(channels.get).toHaveBeenCalledWith('chan-1');
		await expect.element(page.getByText('Kickoff message')).toBeInTheDocument();
		await expect.element(page.getByText('1 conversation', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText(/Researcher/)).toBeInTheDocument();
	});

	it('says so on the thread card when the latest run is still running or failed', async () => {
		const stuckRun: RunTrace = {
			id: 'trace-1',
			trigger_type: 'message',
			label: 'Kickoff message',
			rivulet_id: 'riv-1',
			channel_id: 'chan-1',
			status: 'running',
			span_count: 0,
			total_cost_usd: null,
			total_tokens: 0,
			started_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
			completed_at: null
		};
		seed({ rivulets: [kickoffRivulet] });
		vi.mocked(runs.list).mockResolvedValue([stuckRun]);

		render(ChannelPage);

		await expect.element(page.getByText('Last run is stuck — no steps recorded.')).toBeInTheDocument();
	});

	it('shows the empty state when the channel has no conversations', async () => {
		seed();

		render(ChannelPage);

		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();
	});

	it('banners instead of Routes to when the routed team is unregistered', async () => {
		const assistant: Agent = {
			id: 'agent-1',
			name: 'Assistant',
			description: 'Generalist',
			instructions: 'Help.',
			model: 'auto',
			fallback_models: [],
			approved_for_unattended_tools: false,
			agentos_agent_id: null
		};
		seed({
			channel: { ...generalChannel, team_id: 'team-1' },
			teams: [supportTeam]
		});
		vi.mocked(teams.get).mockResolvedValue({ ...supportTeam, agent_ids: ['agent-1'] });
		vi.mocked(agents.list).mockResolvedValue([assistant]);

		render(ChannelPage);

		await expect
			.element(page.getByText(/Agents aren't ready to run on this node/))
			.toBeInTheDocument();
		await expect
			.element(page.getByText("Agents aren't ready to run — sign out and back in"))
			.toBeInTheDocument();
		await expect.element(page.getByText('Routes to Support')).not.toBeInTheDocument();
	});

	it('warns in the helper line when the channel has no team', async () => {
		seed();

		render(ChannelPage);

		await expect
			.element(page.getByText("No team — agents won't answer").first())
			.toBeInTheDocument();
	});

	it('changes the routed team via the team chip menu', async () => {
		seed({ teams: [supportTeam] });
		vi.mocked(channels.update).mockResolvedValueOnce({ ...generalChannel, team_id: 'team-1' });
		vi.mocked(teams.get).mockResolvedValue({ ...supportTeam, agent_ids: [] });

		render(ChannelPage);
		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();

		await page
			.getByRole('button', { name: /No team/ })
			.first()
			.click();
		await page.getByRole('menuitem', { name: 'Support' }).click();

		expect(channels.update).toHaveBeenCalledWith('chan-1', { team_id: 'team-1' });
		await expect.element(page.getByText('Routes to Support')).toBeInTheDocument();
	});

	it('shows a plain-language error when changing the team fails', async () => {
		seed({ teams: [supportTeam] });
		vi.mocked(channels.update).mockRejectedValueOnce(new Error('boom'));

		render(ChannelPage);
		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();

		await page
			.getByRole('button', { name: /No team/ })
			.first()
			.click();
		await page.getByRole('menuitem', { name: 'Support' }).click();

		await expect
			.element(page.getByText("Couldn't change the team. Try again."))
			.toBeInTheDocument();
	});

	it('posting creates a conversation and opens it — never a channel transcript', async () => {
		seed();
		vi.mocked(rivulets.create).mockResolvedValueOnce(kickoffRivulet);

		render(ChannelPage);
		const input = page.getByPlaceholder('Start a conversation…');
		await expect.element(input).toBeInTheDocument();

		await input.fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.create).toHaveBeenCalledWith('chan-1', 'Kickoff message', []);
		expect(goto).toHaveBeenCalledWith('/channels/chan-1/rivulets/riv-1');
	});

	it('shows a quiet error with retry when the channel fails to load', async () => {
		vi.mocked(channels.get).mockRejectedValueOnce(new Error('boom'));
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(workflowsApi.list).mockResolvedValue([]);

		render(ChannelPage);

		await expect.element(page.getByText("Couldn't load conversations.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});

	it('shows the channel description in the header when set', async () => {
		seed({ channel: { ...generalChannel, description: 'General discussion' } });

		render(ChannelPage);

		await expect
			.element(page.getByText('General discussion', { exact: false }))
			.toBeInTheDocument();
	});

	it('adds a pending file via the attach input and removes it', async () => {
		seed();

		const { container } = await render(ChannelPage);
		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove notes.txt' }).click();
		await expect.element(page.getByText('notes.txt')).not.toBeInTheDocument();
	});

	it('uploads pending files before posting and includes their ids', async () => {
		seed();
		vi.mocked(filesApi.upload).mockResolvedValue({
			file_id: 'file-1',
			content_hash: 'hash',
			filename: 'notes.txt',
			mime_type: 'text/plain',
			size_bytes: 5
		});
		vi.mocked(rivulets.create).mockResolvedValueOnce(kickoffRivulet);

		const { container } = await render(ChannelPage);
		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));
		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByPlaceholder('Start a conversation…').fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(filesApi.upload).toHaveBeenCalledTimes(1);
		expect(rivulets.create).toHaveBeenCalledWith('chan-1', 'Kickoff message', ['file-1']);
	});

	it('does nothing when Send is pressed with no message and no pending files', async () => {
		seed();

		render(ChannelPage);
		await expect
			.element(page.getByText('Start the first conversation in #general.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.create).not.toHaveBeenCalled();
	});

	it('shows a plain-language error when posting fails', async () => {
		seed();
		vi.mocked(rivulets.create).mockRejectedValueOnce(new Error('boom'));

		render(ChannelPage);
		await page.getByPlaceholder('Start a conversation…').fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect.element(page.getByText("Couldn't send that. Try again.")).toBeInTheDocument();
	});
});
