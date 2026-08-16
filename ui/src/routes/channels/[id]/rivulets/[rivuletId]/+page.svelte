<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { channels, type Channel } from '$lib/api/channels';
	import { teams, type Team } from '$lib/api/teams';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import { files as filesApi } from '$lib/api/files';
	import { sync } from '$lib/api/sync';
	import { auth } from '$lib/api/auth.svelte';
	import { agentInkMap, INK_AVATAR, INK_BUBBLE, HUMAN_AVATAR, type AgentInk } from '$lib/ink';
	import { formatBytes, formatClock } from '$lib/format';
	import { renderMarkdown } from '$lib/markdown';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import StreamBar from '$lib/ui/StreamBar.svelte';

	// Rivulet page (06-screens.md → Rivulet, mockups 1g/2o): the full
	// conversation. The Stream Bar here REPLIES in place — new conversations
	// are started from the channel page. Back goes to the channel, not home.

	let channel = $state<Channel | null>(null);
	let rivulet = $state<Rivulet | null>(null);
	let messages = $state<Message[]>([]);
	let teamList = $state<Team[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let loadError = $state<string | null>(null);
	let sending = $state(false);
	let sendError = $state<string | null>(null);
	let resuming = $state(false);
	let resumeError = $state<string | null>(null);
	let downloadError = $state<string | null>(null);
	let scroller = $state<HTMLDivElement | null>(null);
	// Issue #10: this node's own node_id, fetched once, so a remotely
	// executed reply's "ran on" note only shows when it actually differs
	// -- avoids noise on the common (local-execution) case.
	let myNodeId = $state<string | null>(null);

	sync
		.status()
		.then((status) => {
			myNodeId = status.node_id;
		})
		.catch(() => {}); // best-effort -- note just stays hidden if this fails

	function shortNodeId(id: string): string {
		return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id;
	}

	async function handleDownload(attachment: Message['attachments'][number]) {
		downloadError = null;
		try {
			await filesApi.download(attachment.file_id, attachment.filename);
		} catch {
			downloadError = "Couldn't download that file. Try again.";
		}
	}

	// R-9's "agent status indicators" (#30): what an agent is doing before
	// its first token streams — set from agent_status events, cleared to
	// 'streaming' the moment real content starts arriving.
	type LiveStatus = 'thinking' | 'executing_tool' | 'waiting_for_handoff' | 'streaming';

	// Filled in token-by-token from the SSE stream below (FR-12.3) while an
	// agent is mid-run, then cleared once its agent_message event lands —
	// at which point the message is already in `messages` too, either from
	// this same event or from the refetch handleReply does once its POST
	// resolves (dispatch runs synchronously server-side; SSE is what makes
	// the wait visible incrementally instead of as one silent pause).
	let liveMessage = $state<{
		agentId: string;
		agentName: string;
		content: string;
		status: LiveStatus;
		statusDetail: string | null;
	} | null>(null);

	// Copy deck: "Thinking…" / "Using web search…" / "Handing off…". Never
	// renders tool args, only the tool name the backend already limited
	// itself to forwarding.
	function statusLabel(status: LiveStatus, detail: string | null): string {
		if (status === 'executing_tool') return detail ? `Using ${detail}…` : 'Using a tool…';
		if (status === 'waiting_for_handoff') return 'Handing off…';
		return 'Thinking…';
	}

	let pauseNotice = $derived(
		[...messages].reverse().find((m) => m.content_type === 'system_alert')?.content ?? null
	);

	// Includes the currently-streaming agent (if any) so it gets a stable ink
	// immediately, before its message is persisted to `messages`.
	let inkMap = $derived(
		agentInkMap([
			...messages,
			...(liveMessage ? [{ sender_type: 'agent' as const, sender_id: liveMessage.agentId }] : [])
		])
	);

	let title = $derived(
		rivulet?.title || messages.find((m) => m.sender_type === 'human')?.content || 'Conversation'
	);

	// inkMap already holds one entry per distinct agent sender_id, in
	// first-appearance order — just attach each one's display name.
	let participants = $derived.by(() => {
		const list: { name: string; ink: AgentInk }[] = [];
		for (const [senderId, ink] of inkMap) {
			const name = messages.find((m) => m.sender_id === senderId)?.sender_name;
			if (name) list.push({ name, ink });
		}
		return list;
	});

	let humanName = $derived(messages.find((m) => m.sender_type === 'human')?.sender_name ?? null);

	let routedTeam = $derived(teamList.find((t) => t.id === channel?.team_id) ?? null);
	let helper = $derived(
		routedTeam
			? `Routes to ${routedTeam.name} · type / to run a workflow`
			: "No team — agents won't answer"
	);

	async function load(rivuletId: string, channelId: string) {
		loadError = null;
		try {
			const [loadedChannel, loadedRivulet, loadedMessages, loadedTeams, loadedWorkflows] =
				await Promise.all([
					channels.get(channelId),
					rivulets.get(rivuletId),
					rivulets.listMessages(rivuletId),
					teams.list().catch(() => [] as Team[]),
					workflows.list().catch(() => [] as Workflow[])
				]);
			channel = loadedChannel;
			rivulet = loadedRivulet;
			messages = loadedMessages;
			teamList = loadedTeams;
			workflowList = loadedWorkflows;
		} catch {
			loadError = "Couldn't load this conversation.";
		}
	}

	$effect(() => {
		load(page.params.rivuletId!, page.params.id!);
	});

	// Keep the newest message in view as replies arrive or stream in.
	$effect(() => {
		void messages.length;
		void liveMessage?.content;
		if (scroller) scroller.scrollTop = scroller.scrollHeight;
	});

	$effect(() => {
		const rivuletId = page.params.rivuletId!;
		if (!auth.token) return;

		let source: EventSource | null = null;
		let cancelled = false;

		// EventSource can't set an Authorization header, so *some* token has
		// to go in the query string for this one endpoint (api/deps.py's
		// get_current_workspace_id_for_stream) — everything else keeps
		// header-only auth. mintStreamTicket() exchanges the real session
		// token for a short-lived, single-purpose one first, so what
		// actually ends up in this URL (and therefore in server logs/
		// browser history) is far less valuable than the session token
		// itself would be.
		auth
			.mintStreamTicket()
			.then((ticket) => {
				if (cancelled) return;
				source = connectStream(rivuletId, ticket);
			})
			.catch(() => {}); // best-effort -- live streaming just won't start this time

		return () => {
			cancelled = true;
			source?.close();
		};
	});

	function connectStream(rivuletId: string, ticket: string): EventSource {
		const source = new EventSource(
			`/api/v1/rivulets/${rivuletId}/stream?token=${encodeURIComponent(ticket)}`
		);

		// Arrives before an agent's first token (invocation, tool calls,
		// handoff) — see run_agent's on_status in agentos/service.py. Content
		// deltas below take over as soon as they start arriving, so this
		// only ever drives the pre-content status pill.
		source.addEventListener('agent_status', (event) => {
			const data = JSON.parse((event as MessageEvent).data);
			if (!liveMessage || liveMessage.agentId !== data.agent_id) {
				liveMessage = {
					agentId: data.agent_id,
					agentName: data.agent_name,
					content: '',
					status: data.status,
					statusDetail: data.detail
				};
			} else {
				liveMessage.status = data.status;
				liveMessage.statusDetail = data.detail;
			}
		});

		source.addEventListener('agent_token', (event) => {
			const data = JSON.parse((event as MessageEvent).data);
			if (!liveMessage || liveMessage.agentId !== data.agent_id) {
				liveMessage = {
					agentId: data.agent_id,
					agentName: data.agent_name,
					content: '',
					status: 'streaming',
					statusDetail: null
				};
			}
			liveMessage.content += data.token;
			liveMessage.status = 'streaming';
		});

		source.addEventListener('agent_message', () => {
			liveMessage = null;
		});

		source.addEventListener('system_alert', () => {
			liveMessage = null;
		});

		// The handoff message itself is a persisted Message row (content_type
		// 'handoff', rendered as a divider below), which the post-POST
		// refetch in handleReply already picks up — this listener only needs
		// to stop showing a stale "still typing" bubble for the handing-off
		// agent once the handoff fires.
		source.addEventListener('handoff', () => {
			liveMessage = null;
		});

		// 'error' is both api-design.md's custom event name AND EventSource's
		// own reserved name for connection-level failures, so this also
		// fires on plain network hiccups, not just our agent-run-failed
		// payloads — deliberately not parsing event.data here since its
		// shape differs between the two cases. Agent failures are already
		// visible durably via the persisted system_alert message (which
		// clears liveMessage on its own listener above and shows up on the
		// next messages refetch), so this listener only needs to stop
		// showing a stale "still typing" bubble.
		source.addEventListener('error', () => {
			liveMessage = null;
		});

		return source;
	}

	async function handleReply(text: string, files: File[]): Promise<boolean> {
		const rivuletId = page.params.rivuletId!;
		sending = true;
		sendError = null;
		try {
			// Uploads happen first, as their own step — a file has to exist on
			// the server (POST /files/upload) before it can be referenced by
			// file_id in the message body that attaches it.
			const uploaded = await Promise.all(files.map((f) => filesApi.upload(f)));
			// The backend runs the dispatcher + any matched agent synchronously
			// before responding (dispatch/service.py), so re-fetching right
			// after this resolves already picks up an agent's reply — no
			// polling or SSE needed for the reply to show up.
			await rivulets.postMessage(
				rivuletId,
				text,
				uploaded.map((f) => f.file_id)
			);
			messages = await rivulets.listMessages(rivuletId);
			return true;
		} catch {
			sendError = "Couldn't send that. Try again.";
			return false;
		} finally {
			sending = false;
		}
	}

	async function handleResume() {
		const rivuletId = page.params.rivuletId!;
		resuming = true;
		resumeError = null;
		try {
			rivulet = await rivulets.resume(rivuletId);
			messages = await rivulets.listMessages(rivuletId);
		} catch {
			resumeError = "Couldn't resume this conversation. Try again.";
		} finally {
			resuming = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<header class="border-b border-line px-4 pt-7 pb-5 md:px-10 dark:border-line-dark">
		<a
			href={resolve('/channels/[id]', { id: page.params.id! })}
			class="mb-2.5 flex items-center gap-2 text-sm font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
		>
			<Icon name="back" class="h-4 w-4" />
			{channel ? `#${channel.name}` : 'Back'}
		</a>
		<div
			class="max-w-[68ch] font-display text-xl leading-snug font-semibold text-ink dark:text-ink-dark"
		>
			{title}
		</div>
		{#if humanName || participants.length}
			<div class="mt-3.5 flex flex-wrap gap-2">
				{#if humanName}
					<span
						class="flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface py-0 pr-2.5 pl-1 dark:border-line-dark dark:bg-surface-dark"
					>
						<Disc name={humanName} colorClass={HUMAN_AVATAR} size={20} />
						<span class="text-[13px] font-medium text-ink dark:text-ink-dark">{humanName}</span>
					</span>
				{/if}
				{#each participants as p (p.name)}
					<span
						class="flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface py-0 pr-2.5 pl-1 dark:border-line-dark dark:bg-surface-dark"
					>
						<Disc name={p.name} colorClass={INK_AVATAR[p.ink]} size={20} />
						<span class="text-[13px] font-medium text-ink dark:text-ink-dark">{p.name}</span>
					</span>
				{/each}
			</div>
		{/if}
	</header>

	{#if rivulet?.status === 'paused'}
		<div class="px-4 pt-5 md:px-10">
			<div
				class="flex flex-wrap items-center gap-3.5 rounded-xl border border-warn-line bg-warn-soft px-5 py-4 dark:border-warn-line-dark dark:bg-warn-soft-dark"
			>
				<span class="text-[15px] font-semibold text-warn">This conversation is paused.</span>
				{#if pauseNotice}
					<span class="text-sm text-warn-ink dark:text-warn-ink-dark">{pauseNotice}</span>
				{/if}
				<Button variant="warn" class="ml-auto" onclick={handleResume} disabled={resuming}>
					{resuming ? 'Resuming…' : 'Resume'}
				</Button>
			</div>
			{#if resumeError}
				<p class="mt-2 text-sm text-danger">{resumeError}</p>
			{/if}
		</div>
	{/if}

	<div bind:this={scroller} class="flex-1 overflow-y-auto px-4 py-7 md:px-10">
		{#if loadError}
			<ErrorBanner
				message={loadError}
				onRetry={() => load(page.params.rivuletId!, page.params.id!)}
			/>
		{:else}
			{#if downloadError}
				<p class="mb-3 text-sm text-danger">{downloadError}</p>
			{/if}
			<div class="flex flex-col gap-6">
				{#each messages as message (message.id)}
					{#if message.content_type === 'handoff'}
						<!-- Handoff as a first-class event: dashed agent-b rule + the
						     words "Handed off", never just another chat bubble. -->
						<div class="flex items-center gap-3.5 px-4 md:px-11">
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
							<span class="text-[13px] whitespace-nowrap text-muted dark:text-muted-dark">
								<strong class="text-agent-b">Handed off</strong> · {message.content}
							</span>
							<span class="flex-1 border-t-2 border-dashed border-agent-b opacity-50"></span>
						</div>
					{:else if message.content_type === 'workflow_step'}
						<!-- Workflow steps read as a quiet rail event, not a bubble. -->
						<div
							class="flex items-center gap-2.5 px-4 font-mono text-[13px] text-muted md:px-11 dark:text-muted-dark"
						>
							<span aria-hidden="true">▶</span>
							{message.content}
						</div>
					{:else if message.content_type === 'system_alert' || message.sender_type === 'system'}
						<div
							class="max-w-[68ch] rounded-xl border border-warn-line bg-warn-soft px-4.5 py-3 text-[15px] text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
						>
							{message.content}
						</div>
					{:else}
						{@const ink =
							message.sender_type === 'agent' && message.sender_id
								? inkMap.get(message.sender_id)
								: null}
						<div class="flex max-w-[68ch] gap-3.5">
							<Disc
								name={message.sender_name}
								colorClass={ink ? INK_AVATAR[ink] : HUMAN_AVATAR}
								size={32}
							/>
							<div class="min-w-0">
								<div class="mb-1 text-sm">
									<strong class="text-ink dark:text-ink-dark">{message.sender_name}</strong>
									<span class="text-muted dark:text-muted-dark">
										· {formatClock(message.created_at)}</span
									>
									{#if message.model_used}
										<span
											class="cursor-help font-mono text-xs text-muted dark:text-muted-dark"
											title="This reply was generated by {message.model_used}, chosen automatically for {message.tier ===
											'cheap'
												? 'cost'
												: 'quality'} based on how complex your message seemed."
										>
											· {message.model_used}
										</span>
									{/if}
									{#if message.executed_node_id && message.executed_node_id !== myNodeId}
										<span
											class="cursor-help text-xs text-muted dark:text-muted-dark"
											title="This reply ran on a different machine in your synced workspace (id: {message.executed_node_id}), not this one."
										>
											· ran on {shortNodeId(message.executed_node_id)}
										</span>
									{/if}
									{#if message.served_model}
										<span
											class="cursor-help font-mono text-xs text-warn"
											title="The configured model failed with a retryable error, so this reply came from its backup model instead."
										>
											· backup: {message.served_model}
										</span>
									{/if}
								</div>
								{#if message.sender_type === 'agent'}
									<div
										class="msg-md rounded-tl-[4px] rounded-tr-[18px] rounded-b-[18px] px-4.5 py-3.5 text-base leading-normal text-ink dark:text-ink-dark {ink
											? INK_BUBBLE[ink]
											: 'bg-surface dark:bg-surface-dark'}"
									>
										<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes all input before building tags -->
										{@html renderMarkdown(message.content)}
									</div>
								{:else}
									<div class="msg-md text-base leading-normal text-ink dark:text-ink-dark">
										<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes all input before building tags -->
										{@html renderMarkdown(message.content)}
									</div>
								{/if}
								{#if message.attachments.length > 0}
									<div class="mt-2 flex flex-col gap-2">
										{#each message.attachments as attachment (attachment.file_id)}
											<button
												onclick={() => handleDownload(attachment)}
												class="flex h-12 w-fit items-center gap-2.5 rounded-lg border border-line bg-surface px-4 text-left hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
											>
												<Icon
													name="attach"
													class="h-4 w-4 flex-none text-muted dark:text-muted-dark"
												/>
												<span class="font-mono text-sm text-ink dark:text-ink-dark">
													{attachment.filename}
												</span>
												<span class="text-[13px] text-muted dark:text-muted-dark">
													{formatBytes(attachment.size_bytes)}
												</span>
											</button>
										{/each}
									</div>
								{/if}
							</div>
						</div>
					{/if}
				{/each}

				{#if liveMessage}
					{@const ink = inkMap.get(liveMessage.agentId)}
					<div class="flex max-w-[68ch] gap-3.5">
						<Disc
							name={liveMessage.agentName}
							colorClass={ink ? INK_AVATAR[ink] : HUMAN_AVATAR}
							size={32}
						/>
						<div class="min-w-0">
							<div class="mb-1.5 flex items-center gap-2.5 text-sm">
								<strong class="text-ink dark:text-ink-dark">{liveMessage.agentName}</strong>
								{#if liveMessage.status !== 'streaming' || !liveMessage.content}
									<span
										class="flex h-6 items-center gap-1.5 rounded-full bg-accent-soft px-2.5 text-[13px] font-semibold text-accent dark:bg-accent-soft-dark dark:text-accent-dark"
									>
										<span class="breath h-1.5 w-1.5 rounded-full bg-current"></span>
										{statusLabel(liveMessage.status, liveMessage.statusDetail)}
									</span>
								{/if}
							</div>
							{#if liveMessage.content}
								<div
									class="rounded-tl-[4px] rounded-tr-[18px] rounded-b-[18px] px-4.5 py-3.5 text-base leading-normal whitespace-pre-wrap text-ink dark:text-ink-dark {ink
										? INK_BUBBLE[ink]
										: 'bg-surface dark:bg-surface-dark'}"
								>
									{liveMessage.content}<span
										class="caret-blink ml-0.5 inline-block h-[18px] w-0.5 translate-y-[3px] bg-accent dark:bg-accent-dark"
									></span>
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div class="px-4 pb-24 md:px-10 md:pb-7">
		<StreamBar
			placeholder="Reply to this conversation…"
			{helper}
			busy={sending}
			error={sendError}
			slashWorkflows={workflowList}
			onSend={handleReply}
		/>
	</div>
</div>

<style>
	/* Markdown inside chat messages: readable, not a blog. */
	.msg-md :global(p) {
		margin: 0;
	}
	.msg-md :global(p + p),
	.msg-md :global(ul),
	.msg-md :global(ol),
	.msg-md :global(pre),
	.msg-md :global(blockquote),
	.msg-md :global(h1),
	.msg-md :global(h2),
	.msg-md :global(h3) {
		margin-top: 0.6em;
	}
	.msg-md :global(h1),
	.msg-md :global(h2) {
		font-size: 1.1em;
		font-weight: 600;
	}
	.msg-md :global(h3) {
		font-size: 1em;
		font-weight: 600;
	}
	.msg-md :global(ul) {
		list-style: disc;
		padding-left: 1.4em;
	}
	.msg-md :global(ol) {
		list-style: decimal;
		padding-left: 1.4em;
	}
	.msg-md :global(code) {
		font-family: var(--font-mono);
		font-size: 0.85em;
		background: color-mix(in srgb, currentColor 8%, transparent);
		border-radius: 6px;
		padding: 0.1em 0.35em;
	}
	.msg-md :global(pre) {
		background: color-mix(in srgb, currentColor 6%, transparent);
		border-radius: 12px;
		padding: 0.8em 1em;
		overflow-x: auto;
	}
	.msg-md :global(pre code) {
		background: none;
		padding: 0;
		font-size: 0.85em;
	}
	.msg-md :global(blockquote) {
		border-left: 3px solid var(--color-line);
		padding-left: 0.9em;
		color: var(--color-muted);
	}
	.msg-md :global(a) {
		color: var(--color-accent);
		text-decoration: underline;
	}
</style>
