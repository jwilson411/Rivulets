// Browser-mode component test (see agents/agentsPage.svelte.test.ts and
// Sidebar.svelte.test.ts for the $app/state + $app/paths mocking pattern).
// This route reads channel/rivulet ids from `page.params` and opens an
// EventSource for live agent-token streaming (FR-12.3, the recent "agent
// status indicators" work) whenever `auth.token` is set -- both are mocked
// below. A hand-rolled FakeEventSource stands in for the real browser
// EventSource so streaming events can be dispatched deterministically
// instead of depending on a real SSE connection.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RivuletPage from './+page.svelte';
import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
import { channels, type Channel } from '$lib/api/channels';

vi.mock('$app/state', () => ({
	page: {
		params: { id: 'chan-1', rivuletId: 'riv-1' },
		url: new URL('http://localhost/channels/chan-1/rivulets/riv-1')
	}
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

const authState = vi.hoisted(() => ({ token: null as string | null }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get token() {
			return authState.token;
		}
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { get: vi.fn() }
}));

vi.mock('$lib/api/rivulets', () => ({
	rivulets: { get: vi.fn(), listMessages: vi.fn(), postMessage: vi.fn(), resume: vi.fn() }
}));

vi.mock('$lib/api/files', () => ({
	files: { upload: vi.fn(), download: vi.fn() }
}));

class FakeEventSource {
	static instances: FakeEventSource[] = [];
	url: string;
	closed = false;
	private listeners: Record<string, ((event: MessageEvent) => void)[]> = {};

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	addEventListener(type: string, cb: (event: MessageEvent) => void) {
		(this.listeners[type] ??= []).push(cb);
	}

	removeEventListener(type: string, cb: (event: MessageEvent) => void) {
		this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== cb);
	}

	close() {
		this.closed = true;
	}

	emit(type: string, data: unknown) {
		for (const cb of this.listeners[type] ?? []) {
			cb({ data: JSON.stringify(data) } as MessageEvent);
		}
	}
}

vi.stubGlobal('EventSource', FakeEventSource);

const generalChannel: Channel = {
	id: 'chan-1',
	name: 'general',
	description: null,
	team_id: null,
	position: 0,
	archived: false
};

const activeRivulet: Rivulet = {
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
	tier: null
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
	tier: null
};

afterEach(() => {
	vi.clearAllMocks();
	authState.token = null;
	FakeEventSource.instances = [];
});

describe('channels/[id]/rivulets/[rivuletId]/+page.svelte', () => {
	it('loads the channel, rivulet, and messages by their route params', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, agentMessage]);

		render(RivuletPage);

		expect(channels.get).toHaveBeenCalledWith('chan-1');
		expect(rivulets.get).toHaveBeenCalledWith('riv-1');
		await expect.element(page.getByText('#general', { exact: false })).toBeInTheDocument();
		// "Kickoff message" appears twice: as the derived title (h1, since
		// this rivulet has no explicit title) and as the human message body.
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();
		await expect.element(page.getByText('Kickoff message').last()).toBeInTheDocument();
		await expect.element(page.getByText('On it')).toBeInTheDocument();
	});

	it('sends a reply via rivulets.postMessage and refetches messages', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages)
			.mockResolvedValueOnce([humanMessage])
			.mockResolvedValueOnce([humanMessage, agentMessage]);
		vi.mocked(rivulets.postMessage).mockResolvedValueOnce(agentMessage);

		render(RivuletPage);
		const input = page.getByPlaceholder('Reply to this rivulet…');
		await expect.element(input).toBeInTheDocument();

		await input.fill('Sounds good');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-1', 'Sounds good', []);
		await expect.element(input).toHaveValue('');
		await expect.element(page.getByText('On it')).toBeInTheDocument();
	});

	it('shows a pause banner and resumes via rivulets.resume', async () => {
		const pausedRivulet: Rivulet = { ...activeRivulet, status: 'paused' };
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(pausedRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);
		vi.mocked(rivulets.resume).mockResolvedValueOnce(activeRivulet);

		render(RivuletPage);
		await expect
			.element(page.getByText('This rivulet is paused — agent replies are suppressed.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Resume' }).click();

		expect(rivulets.resume).toHaveBeenCalledWith('riv-1');
	});

	it('renders streamed agent tokens live from the SSE connection', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();

		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];
		expect(source.url).toContain('/api/v1/rivulets/riv-1/stream?token=test-token');

		source.emit('agent_token', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			token: 'Thinking'
		});
		source.emit('agent_token', { agent_id: 'agent-1', agent_name: 'Researcher', token: '...' });

		await expect.element(page.getByText('Thinking...')).toBeInTheDocument();

		source.emit('agent_message', {});
		await expect.element(page.getByText('Thinking...')).not.toBeInTheDocument();
	});

	it('shows an error when the rivulet fails to load', async () => {
		vi.mocked(channels.get).mockRejectedValueOnce(new Error('Failed to load rivulet'));
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([]);

		render(RivuletPage);

		await expect.element(page.getByText('Failed to load rivulet')).toBeInTheDocument();
	});
});
