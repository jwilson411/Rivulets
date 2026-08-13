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
import { files as filesApi } from '$lib/api/files';

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
		},
		// The component exchanges the session token for one of these before
		// opening the EventSource (see +page.svelte's mintStreamTicket call)
		// -- returning a fixed, distinct value here (rather than echoing
		// authState.token) is what proves the FakeEventSource URL assertion
		// below is actually observing the ticket, not the raw session token.
		mintStreamTicket: vi.fn(async () => 'test-ticket')
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

const attachedMessage: Message = {
	id: 'msg-3',
	rivulet_id: 'riv-1',
	sender_type: 'human',
	sender_id: null,
	sender_name: 'Justin',
	content: 'See attached',
	content_type: 'text',
	created_at: new Date().toISOString(),
	attachments: [
		{ file_id: 'file-9', filename: 'report.pdf', mime_type: 'application/pdf', size_bytes: 2048 }
	],
	model_used: null,
	tier: null,
	executed_node_id: null,
	served_model: null
};

const handoffMessage: Message = {
	id: 'msg-h',
	rivulet_id: 'riv-1',
	sender_type: 'system',
	sender_id: null,
	sender_name: 'System',
	content: 'Researcher handed off to Writer',
	content_type: 'handoff',
	created_at: new Date().toISOString(),
	attachments: [],
	model_used: null,
	tier: null,
	executed_node_id: null,
	served_model: null
};

const systemAlertMessage: Message = {
	id: 'msg-sys',
	rivulet_id: 'riv-1',
	sender_type: 'system',
	sender_id: null,
	sender_name: 'System',
	content: 'Agent Researcher was paused after an error',
	content_type: 'system_alert',
	created_at: new Date().toISOString(),
	attachments: [],
	model_used: null,
	tier: null,
	executed_node_id: null,
	served_model: null
};

const autoModeMessage: Message = {
	...agentMessage,
	id: 'msg-auto',
	content: 'Here is the answer',
	model_used: 'claude-haiku-4-5',
	tier: 'cheap'
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
		// The URL carries the short-lived ticket mintStreamTicket() resolved
		// to, never the raw session token (authState.token above).
		expect(source.url).toContain('/api/v1/rivulets/riv-1/stream?token=test-ticket');

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

	it('adds a pending file via the file input, shows it as a chip, and removes it', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		const { container } = await render(RivuletPage);
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove notes.txt' }).click();
		await expect.element(page.getByText('notes.txt')).not.toBeInTheDocument();
	});

	it('uploads pending files before sending and includes their ids in the message', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages)
			.mockResolvedValueOnce([humanMessage])
			.mockResolvedValueOnce([humanMessage, agentMessage]);
		vi.mocked(filesApi.upload).mockResolvedValue({
			file_id: 'file-1',
			content_hash: 'hash',
			filename: 'notes.txt',
			mime_type: 'text/plain',
			size_bytes: 5
		});
		vi.mocked(rivulets.postMessage).mockResolvedValueOnce(agentMessage);

		const { container } = await render(RivuletPage);
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));
		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByPlaceholder('Reply to this rivulet…').fill('See attached');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(filesApi.upload).toHaveBeenCalledTimes(1);
		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-1', 'See attached', ['file-1']);
	});

	it('does nothing when the form is submitted with no reply text and no pending files', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		const { container } = await render(RivuletPage);
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();

		// The Send button is disabled in this state, so drive the guard
		// inside handleReply directly via a native form submission instead.
		const form = container.querySelector('form')!;
		form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));

		expect(rivulets.postMessage).not.toHaveBeenCalled();
	});

	it('shows an error when sending a reply fails', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);
		vi.mocked(rivulets.postMessage).mockRejectedValueOnce(new Error('Failed to send message'));

		render(RivuletPage);
		await expect
			.element(page.getByRole('heading', { name: 'Kickoff message' }))
			.toBeInTheDocument();

		await page.getByPlaceholder('Reply to this rivulet…').fill('Hello');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect.element(page.getByText('Failed to send message')).toBeInTheDocument();
		// A send failure must not hide the existing transcript behind the
		// error -- it's a distinct `sendError`, not a reuse of `loadError`.
		// (Rivulet has no title, so "Kickoff message" also matches the <h1>
		// derived from it -- .last() targets the transcript message itself.)
		await expect.element(page.getByText('Kickoff message').last()).toBeInTheDocument();
	});

	it('shows an error on the banner when resume fails, without throwing', async () => {
		const pausedRivulet: Rivulet = { ...activeRivulet, status: 'paused' };
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(pausedRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);
		vi.mocked(rivulets.resume).mockRejectedValueOnce(new Error('Failed to resume rivulet'));

		render(RivuletPage);
		await expect
			.element(page.getByText('This rivulet is paused — agent replies are suppressed.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Resume' }).click();

		expect(rivulets.resume).toHaveBeenCalledWith('riv-1');
		await expect.element(page.getByText('Failed to resume rivulet')).toBeInTheDocument();
		// The banner (and its "still paused" implication) stays up rather than
		// leaving the user staring at a resume button with no explanation.
		await expect
			.element(page.getByText('This rivulet is paused — agent replies are suppressed.'))
			.toBeInTheDocument();
	});

	it('downloads an attachment when clicked', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([attachedMessage]);
		vi.mocked(filesApi.download).mockResolvedValueOnce(undefined);

		render(RivuletPage);
		await expect.element(page.getByRole('button', { name: /report\.pdf/ })).toBeInTheDocument();

		await page.getByRole('button', { name: /report\.pdf/ }).click();

		expect(filesApi.download).toHaveBeenCalledWith('file-9', 'report.pdf');
	});

	it('shows a download error when the download fails', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([attachedMessage]);
		vi.mocked(filesApi.download).mockRejectedValueOnce(new Error('Failed to download file'));

		render(RivuletPage);
		await expect.element(page.getByRole('button', { name: /report\.pdf/ })).toBeInTheDocument();

		await page.getByRole('button', { name: /report\.pdf/ }).click();

		await expect.element(page.getByText('Failed to download file')).toBeInTheDocument();
	});

	it('renders a handoff message as a divider', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, handoffMessage]);

		render(RivuletPage);

		await expect.element(page.getByText('handoff')).toBeInTheDocument();
		await expect
			.element(page.getByText('Researcher handed off to Writer', { exact: false }))
			.toBeInTheDocument();
	});

	it('shows the model used and tier for an auto-mode reply', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, autoModeMessage]);

		render(RivuletPage);

		await expect
			.element(page.getByText('via claude-haiku-4-5', { exact: false }))
			.toBeInTheDocument();
	});

	it("shows a fallback badge when a reply came from the agent's fallback chain (#103)", async () => {
		const fallbackMessage: Message = {
			...agentMessage,
			id: 'msg-fallback',
			content: 'Answered via backup',
			served_model: 'openai:gpt-4o-mini'
		};
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, fallbackMessage]);

		render(RivuletPage);

		await expect
			.element(page.getByText('fallback: openai:gpt-4o-mini', { exact: false }))
			.toBeInTheDocument();
	});

	it('renders system alert messages distinctly from human/agent messages', async () => {
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage, systemAlertMessage]);

		render(RivuletPage);

		await expect
			.element(page.getByText('Agent Researcher was paused after an error'))
			.toBeInTheDocument();
	});

	it('shows an executing_tool status pill with the tool name from an agent_status event', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'executing_tool',
			detail: 'search_web'
		});

		await expect.element(page.getByText('using search_web…')).toBeInTheDocument();
	});

	it('shows a waiting_for_handoff status pill', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'waiting_for_handoff',
			detail: null
		});

		await expect.element(page.getByText('handing off…')).toBeInTheDocument();
	});

	it('shows a thinking status pill, then updates it in place for a second event from the same agent', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('thinking…')).toBeInTheDocument();

		// Same agent id as before -> updates the existing liveMessage's status
		// in place instead of creating a second one.
		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'executing_tool',
			detail: 'search_web'
		});
		await expect.element(page.getByText('using search_web…')).toBeInTheDocument();
	});

	it('clears the live status pill on a handoff event', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('thinking…')).toBeInTheDocument();

		source.emit('handoff', {});
		await expect.element(page.getByText('thinking…')).not.toBeInTheDocument();
	});

	it('clears the live status pill on a system_alert event', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('thinking…')).toBeInTheDocument();

		source.emit('system_alert', {});
		await expect.element(page.getByText('thinking…')).not.toBeInTheDocument();
	});

	it('clears the live status pill on a connection error event', async () => {
		authState.token = 'test-token';
		vi.mocked(channels.get).mockResolvedValue(generalChannel);
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('thinking…')).toBeInTheDocument();

		source.emit('error', {});
		await expect.element(page.getByText('thinking…')).not.toBeInTheDocument();
	});
});
