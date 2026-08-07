// Browser-mode component test (see agents/agentsPage.svelte.test.ts and
// Sidebar.svelte.test.ts for the $app/state + $app/paths mocking pattern
// route components need). This route reads its channel id from
// `page.params.id` (SvelteKit's $app/state), so that's mocked alongside
// the $lib/api/* modules it calls. $lib/ink and $lib/format are real,
// unmocked utility modules -- no network I/O, safe to exercise as-is.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ChannelPage from './+page.svelte';
import { channels, type Channel } from '$lib/api/channels';
import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
import { teams, type Team } from '$lib/api/teams';
import { files as filesApi } from '$lib/api/files';

vi.mock('$app/state', () => ({
	page: { params: { id: 'chan-1' }, url: new URL('http://localhost/channels/chan-1') }
}));

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
	teams: { list: vi.fn() }
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
	executed_node_id: null
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
	executed_node_id: null
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('channels/[id]/+page.svelte', () => {
	it('loads the channel by page.params.id and shows a rivulet preview', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([kickoffRivulet]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, agentMessage]);

		render(ChannelPage);

		expect(channels.get).toHaveBeenCalledWith('chan-1');
		await expect.element(page.getByText('Kickoff message')).toBeInTheDocument();
		await expect.element(page.getByText('1 rivulet')).toBeInTheDocument();
		await expect.element(page.getByText(/Researcher/)).toBeInTheDocument();
	});

	it('shows the empty state when the channel has no rivulets', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(ChannelPage);

		await expect.element(page.getByText('No rivulets yet — start one below.')).toBeInTheDocument();
	});

	it('changes the routed team via channels.update', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(channels.update).mockResolvedValueOnce({ ...generalChannel, team_id: 'team-1' });

		render(ChannelPage);
		await expect.element(page.getByText('Support')).toBeInTheDocument();

		await page.getByRole('combobox').selectOptions('team-1');

		expect(channels.update).toHaveBeenCalledWith('chan-1', { team_id: 'team-1' });
		await expect.element(page.getByText('routes to', { exact: false })).toBeInTheDocument();
	});

	it('posts a new rivulet via rivulets.create and clears the composer', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel)
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([kickoffRivulet]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);
		vi.mocked(rivulets.create).mockResolvedValueOnce(kickoffRivulet);

		render(ChannelPage);
		const input = page.getByPlaceholder(/Start a rivulet/);
		await expect.element(input).toBeInTheDocument();

		await input.fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.create).toHaveBeenCalledWith('chan-1', 'Kickoff message', []);
		await expect.element(input).toHaveValue('');
	});

	it('shows an error when the channel fails to load', async () => {
		vi.mocked(channels.get).mockRejectedValueOnce(new Error('Failed to load channel'));
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(ChannelPage);

		await expect.element(page.getByText('Failed to load channel')).toBeInTheDocument();
	});

	it('shows the channel description when set', async () => {
		vi.mocked(channels.get).mockResolvedValue({
			...generalChannel,
			description: 'General discussion'
		});
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		render(ChannelPage);

		await expect
			.element(page.getByText('General discussion', { exact: false }))
			.toBeInTheDocument();
	});

	it('shows an error when changing the team fails', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([supportTeam]);
		vi.mocked(channels.update).mockRejectedValueOnce(new Error('Failed to change team'));

		render(ChannelPage);
		await expect.element(page.getByText('Support')).toBeInTheDocument();

		await page.getByRole('combobox').selectOptions('team-1');

		await expect.element(page.getByText('Failed to change team')).toBeInTheDocument();
	});

	it('adds a pending file via the file input and removes it', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		const { container } = await render(ChannelPage);
		await expect.element(page.getByText('No rivulets yet — start one below.')).toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove notes.txt' }).click();
		await expect.element(page.getByText('notes.txt')).not.toBeInTheDocument();
	});

	it('uploads pending files before posting and includes their ids', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel)
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([kickoffRivulet]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);
		vi.mocked(filesApi.upload).mockResolvedValue({
			file_id: 'file-1',
			content_hash: 'hash',
			filename: 'notes.txt',
			mime_type: 'text/plain',
			size_bytes: 5
		});
		vi.mocked(rivulets.create).mockResolvedValueOnce(kickoffRivulet);

		const { container } = await render(ChannelPage);
		await expect.element(page.getByText('No rivulets yet — start one below.')).toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));
		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByPlaceholder(/Start a rivulet/).fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(filesApi.upload).toHaveBeenCalledTimes(1);
		expect(rivulets.create).toHaveBeenCalledWith('chan-1', 'Kickoff message', ['file-1']);
	});

	it('does nothing when the form is submitted with no message and no pending files', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);

		const { container } = await render(ChannelPage);
		await expect.element(page.getByText('No rivulets yet — start one below.')).toBeInTheDocument();

		const form = container.querySelector('form')!;
		form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));

		expect(rivulets.create).not.toHaveBeenCalled();
	});

	it('shows an error when posting a new rivulet fails', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.listForChannel).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(rivulets.create).mockRejectedValueOnce(new Error('Failed to send message'));

		render(ChannelPage);
		await page.getByPlaceholder(/Start a rivulet/).fill('Kickoff message');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect.element(page.getByText('Failed to send message')).toBeInTheDocument();
	});
});
