// Browser-mode component test for the Rivulet page (06-screens.md →
// Rivulet, mockups 1g/2o): the full conversation with handoffs as
// first-class events, live SSE streaming, the paused banner, and a Stream
// Bar that replies in place. A hand-rolled FakeEventSource stands in for
// the real browser EventSource so streaming events can be dispatched
// deterministically.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RivuletPage from './+page.svelte';
import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
import { channels, type Channel } from '$lib/api/channels';
import { files as filesApi } from '$lib/api/files';
import { teams } from '$lib/api/teams';
import { agents } from '$lib/api/agents';
import { workflows } from '$lib/api/workflows';
import { runs } from '$lib/api/runs';

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
		// opening the EventSource -- returning a fixed, distinct value here
		// is what proves the FakeEventSource URL assertion below is actually
		// observing the ticket, not the raw session token.
		mintStreamTicket: vi.fn(async () => 'test-ticket')
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { get: vi.fn() }
}));

vi.mock('$lib/api/rivulets', () => ({
	rivulets: { get: vi.fn(), listMessages: vi.fn(), postMessage: vi.fn(), resume: vi.fn() }
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

vi.mock('$lib/api/sync', () => ({
	sync: { status: vi.fn(async () => ({ node_id: 'node-a1' })) }
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

const workflowStepMessage: Message = {
	id: 'msg-wf',
	rivulet_id: 'riv-1',
	sender_type: 'system',
	sender_id: null,
	sender_name: 'System',
	content: 'retry-check → Summarize',
	content_type: 'workflow_step',
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

function seed(messages: Message[], rivulet: Rivulet = activeRivulet) {
	vi.mocked(channels.get).mockResolvedValue(generalChannel);
	vi.mocked(rivulets.get).mockResolvedValue(rivulet);
	vi.mocked(rivulets.listMessages).mockResolvedValue(messages);
	vi.mocked(teams.list).mockResolvedValue([]);
	vi.mocked(workflows.list).mockResolvedValue([]);
	vi.mocked(runs.list).mockResolvedValue([]);
}

afterEach(() => {
	vi.clearAllMocks();
	authState.token = null;
	FakeEventSource.instances = [];
});

describe('channels/[id]/rivulets/[rivuletId]/+page.svelte', () => {
	it('loads the channel, rivulet, and messages by their route params', async () => {
		seed([humanMessage, agentMessage]);

		render(RivuletPage);

		expect(channels.get).toHaveBeenCalledWith('chan-1');
		expect(rivulets.get).toHaveBeenCalledWith('riv-1');
		await expect.element(page.getByText('#general', { exact: false })).toBeInTheDocument();
		// "Kickoff message" appears twice: as the derived title (since this
		// rivulet has no explicit title) and as the human message body.
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();
		await expect.element(page.getByText('On it')).toBeInTheDocument();
	});

	it('sends a reply via rivulets.postMessage and refetches messages', async () => {
		seed([humanMessage]);
		vi.mocked(rivulets.listMessages)
			.mockResolvedValueOnce([humanMessage])
			.mockResolvedValueOnce([humanMessage, agentMessage]);
		vi.mocked(rivulets.postMessage).mockResolvedValueOnce(agentMessage);

		render(RivuletPage);
		const input = page.getByPlaceholder('Reply to this conversation…');
		await expect.element(input).toBeInTheDocument();

		await input.fill('Sounds good');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-1', 'Sounds good', []);
		await expect.element(input).toHaveValue('');
		await expect.element(page.getByText('On it')).toBeInTheDocument();
	});

	it('shows the paused banner and resumes via rivulets.resume', async () => {
		seed([humanMessage], { ...activeRivulet, status: 'paused' });
		vi.mocked(rivulets.resume).mockResolvedValueOnce(activeRivulet);

		render(RivuletPage);
		await expect.element(page.getByText('This conversation is paused.')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Resume' }).click();

		expect(rivulets.resume).toHaveBeenCalledWith('riv-1');
	});

	it('renders streamed agent tokens live with the streaming caret', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();

		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];
		// The URL carries the short-lived ticket mintStreamTicket() resolved
		// to, never the raw session token (authState.token above).
		expect(source.url).toContain('/api/v1/rivulets/riv-1/stream?token=test-ticket');

		source.emit('agent_token', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			token: 'Looking'
		});
		source.emit('agent_token', { agent_id: 'agent-1', agent_name: 'Researcher', token: '...' });

		await expect.element(page.getByText('Looking...')).toBeInTheDocument();

		source.emit('agent_message', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			message_id: 'msg-live',
			content: 'Looking...'
		});
		// The streamed text must stay visible as a persisted bubble —
		// clearing liveMessage without inserting the row is what made
		// replies blink away until the post-POST refetch.
		await expect.element(page.getByText('Looking...')).toBeInTheDocument();
	});

	it('says so in the header when the latest run is still running with no steps', async () => {
		seed([humanMessage, agentMessage]);
		vi.mocked(runs.list).mockResolvedValue([
			{
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
			}
		]);

		render(RivuletPage);

		await expect
			.element(page.getByText('Last run is still marked running with no steps.'))
			.toBeInTheDocument();
	});

	it('shows a quiet error with a retry when the conversation fails to load', async () => {
		vi.mocked(channels.get).mockRejectedValueOnce(new Error('boom'));
		vi.mocked(rivulets.get).mockResolvedValue(activeRivulet);
		vi.mocked(rivulets.listMessages).mockResolvedValue([]);
		vi.mocked(teams.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);

		render(RivuletPage);

		await expect.element(page.getByText("Couldn't load this conversation.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});

	it('adds a pending file via the attach input, shows it as a chip, and removes it', async () => {
		seed([humanMessage]);

		const { container } = await render(RivuletPage);
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));

		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove notes.txt' }).click();
		await expect.element(page.getByText('notes.txt')).not.toBeInTheDocument();
	});

	it('uploads pending files before sending and includes their ids in the message', async () => {
		seed([humanMessage]);
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
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();

		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		await page
			.elementLocator(input)
			.upload(new File(['hello'], 'notes.txt', { type: 'text/plain' }));
		await expect.element(page.getByText('notes.txt')).toBeInTheDocument();

		await page.getByPlaceholder('Reply to this conversation…').fill('See attached');
		await page.getByRole('button', { name: 'Send' }).click();

		expect(filesApi.upload).toHaveBeenCalledTimes(1);
		expect(rivulets.postMessage).toHaveBeenCalledWith('riv-1', 'See attached', ['file-1']);
	});

	it('does nothing when Send is pressed with no reply text and no pending files', async () => {
		seed([humanMessage]);

		render(RivuletPage);
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();

		await page.getByRole('button', { name: 'Send' }).click();

		expect(rivulets.postMessage).not.toHaveBeenCalled();
	});

	it('shows a plain-language error when sending a reply fails', async () => {
		seed([humanMessage]);
		vi.mocked(rivulets.postMessage).mockRejectedValueOnce(new Error('boom'));

		render(RivuletPage);
		await expect.element(page.getByText('Kickoff message').first()).toBeInTheDocument();

		await page.getByPlaceholder('Reply to this conversation…').fill('Hello');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect.element(page.getByText("Couldn't send that. Try again.")).toBeInTheDocument();
		// A send failure must not hide the existing transcript behind the
		// error -- it's a distinct sendError, not a reuse of loadError.
		await expect.element(page.getByText('Kickoff message').last()).toBeInTheDocument();
	});

	it('shows an error on the banner when resume fails, without throwing', async () => {
		seed([humanMessage], { ...activeRivulet, status: 'paused' });
		vi.mocked(rivulets.resume).mockRejectedValueOnce(new Error('boom'));

		render(RivuletPage);
		await expect.element(page.getByText('This conversation is paused.')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Resume' }).click();

		expect(rivulets.resume).toHaveBeenCalledWith('riv-1');
		await expect
			.element(page.getByText("Couldn't resume this conversation. Try again."))
			.toBeInTheDocument();
		// The banner (and its "still paused" implication) stays up rather than
		// leaving the user staring at a resume button with no explanation.
		await expect.element(page.getByText('This conversation is paused.')).toBeInTheDocument();
	});

	it('downloads an attachment when its 48px file row is clicked', async () => {
		seed([attachedMessage]);
		vi.mocked(filesApi.download).mockResolvedValueOnce(undefined);

		render(RivuletPage);
		await expect.element(page.getByRole('button', { name: /report\.pdf/ })).toBeInTheDocument();

		await page.getByRole('button', { name: /report\.pdf/ }).click();

		expect(filesApi.download).toHaveBeenCalledWith('file-9', 'report.pdf');
	});

	it('shows a download error when the download fails', async () => {
		seed([attachedMessage]);
		vi.mocked(filesApi.download).mockRejectedValueOnce(new Error('boom'));

		render(RivuletPage);
		await expect.element(page.getByRole('button', { name: /report\.pdf/ })).toBeInTheDocument();

		await page.getByRole('button', { name: /report\.pdf/ }).click();

		await expect
			.element(page.getByText("Couldn't download that file. Try again."))
			.toBeInTheDocument();
	});

	it('renders a handoff as a first-class "Handed off" event, not a bubble', async () => {
		seed([humanMessage, handoffMessage]);

		render(RivuletPage);

		await expect.element(page.getByText('Handed off')).toBeInTheDocument();
		await expect
			.element(page.getByText('Researcher handed off to Writer', { exact: false }))
			.toBeInTheDocument();
	});

	it('renders a workflow step as a quiet rail event', async () => {
		seed([humanMessage, workflowStepMessage]);

		render(RivuletPage);

		await expect.element(page.getByText('retry-check → Summarize')).toBeInTheDocument();
	});

	it('renders markdown in message bodies', async () => {
		seed([
			humanMessage,
			{ ...agentMessage, id: 'msg-md', content: 'Use **bold** and `code` here' }
		]);

		render(RivuletPage);

		await expect.element(page.getByText('bold', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('code', { exact: true })).toBeInTheDocument();
	});

	it('shows the model that answered an auto-mode reply', async () => {
		seed([humanMessage, autoModeMessage]);

		render(RivuletPage);

		await expect.element(page.getByText('claude-haiku-4-5', { exact: false })).toBeInTheDocument();
	});

	it("notes when a reply came from the agent's backup model (#103)", async () => {
		seed([
			humanMessage,
			{
				...agentMessage,
				id: 'msg-fallback',
				content: 'Answered',
				served_model: 'openai:gpt-4o-mini'
			}
		]);

		render(RivuletPage);

		await expect
			.element(page.getByText('backup: openai:gpt-4o-mini', { exact: false }))
			.toBeInTheDocument();
	});

	it('renders system alerts as an inline banner, not a chat bubble', async () => {
		seed([humanMessage, systemAlertMessage]);

		render(RivuletPage);

		await expect
			.element(page.getByText('Agent Researcher was paused after an error'))
			.toBeInTheDocument();
	});

	it('shows a "Using …" status pill with the tool name from an agent_status event', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'executing_tool',
			detail: 'search_web'
		});

		await expect.element(page.getByText('Using search_web…')).toBeInTheDocument();
	});

	it('shows a "Handing off…" status pill', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'waiting_for_handoff',
			detail: null
		});

		await expect.element(page.getByText('Handing off…')).toBeInTheDocument();
	});

	it('shows a thinking pill, then updates it in place for a second event from the same agent', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('Thinking…')).toBeInTheDocument();

		// Same agent id as before -> updates the existing liveMessage's status
		// in place instead of creating a second one.
		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'executing_tool',
			detail: 'search_web'
		});
		await expect.element(page.getByText('Using search_web…')).toBeInTheDocument();
	});

	it('clears the live status pill on a handoff event', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('Thinking…')).toBeInTheDocument();

		source.emit('handoff', {});
		await expect.element(page.getByText('Thinking…')).not.toBeInTheDocument();
	});

	it('clears the live status pill on a system_alert event', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('Thinking…')).toBeInTheDocument();

		source.emit('system_alert', {});
		await expect.element(page.getByText('Thinking…')).not.toBeInTheDocument();
	});

	it('clears the live status pill on a connection error event', async () => {
		authState.token = 'test-token';
		seed([humanMessage]);

		render(RivuletPage);
		await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
		const source = FakeEventSource.instances[0];

		source.emit('agent_status', {
			agent_id: 'agent-1',
			agent_name: 'Researcher',
			status: 'thinking',
			detail: null
		});
		await expect.element(page.getByText('Thinking…')).toBeInTheDocument();

		source.emit('error', {});
		await expect.element(page.getByText('Thinking…')).not.toBeInTheDocument();
	});

	it('highlights a resolved @mention and offers teammates in the composer picker', async () => {
		const mentioned: Message = {
			...humanMessage,
			content: '@Assistant ping'
		};
		seed([mentioned]);
		vi.mocked(channels.get).mockResolvedValue({ ...generalChannel, team_id: 'team-1' });
		vi.mocked(teams.list).mockResolvedValue([{ id: 'team-1', name: 'Support', description: null }]);
		vi.mocked(teams.get).mockResolvedValue({
			id: 'team-1',
			name: 'Support',
			description: null,
			agent_ids: ['agent-1']
		});
		vi.mocked(agents.list).mockResolvedValue([
			{
				id: 'agent-1',
				name: 'Assistant',
				description: 'Generalist',
				instructions: 'Help.',
				model: 'auto',
				fallback_models: [],
				approved_for_unattended_tools: false,
				agentos_agent_id: 'agent-1'
			}
		]);

		const { container } = await render(RivuletPage);
		await expect.element(page.getByText(/type @ to mention/)).toBeInTheDocument();
		expect(container.querySelector('.mention')?.textContent).toBe('@Assistant');

		await page.getByPlaceholder('Reply to this conversation…').fill('@');
		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();
	});
});
