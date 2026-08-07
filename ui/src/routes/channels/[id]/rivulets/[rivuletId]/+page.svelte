<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { channels, type Channel } from '$lib/api/channels';
	import { files as filesApi } from '$lib/api/files';
	import { auth } from '$lib/api/auth.svelte';
	import {
		agentInkMap,
		initials,
		INK_AVATAR,
		INK_SPINE,
		INK_NAME_TEXT,
		HUMAN_AVATAR,
		HUMAN_SPINE,
		HUMAN_NAME_TEXT,
		type AgentInk
	} from '$lib/ink';
	import { formatBytes, formatClock } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';

	let channel = $state<Channel | null>(null);
	let rivulet = $state<Rivulet | null>(null);
	let messages = $state<Message[]>([]);
	let reply = $state('');
	let loadError = $state<string | null>(null);
	let sending = $state(false);
	let resuming = $state(false);
	let pendingFiles = $state<globalThis.File[]>([]);
	let fileInput = $state<HTMLInputElement | null>(null);
	let downloadError = $state<string | null>(null);

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		pendingFiles = [...pendingFiles, ...Array.from(input.files ?? [])];
		input.value = ''; // allow re-selecting the same file after removal
	}

	function removePendingFile(index: number) {
		pendingFiles = pendingFiles.filter((_, i) => i !== index);
	}

	async function handleDownload(attachment: Message['attachments'][number]) {
		downloadError = null;
		try {
			await filesApi.download(attachment.file_id, attachment.filename);
		} catch (err) {
			downloadError = err instanceof Error ? err.message : 'Failed to download file';
		}
	}

	// R-9's "agent status indicators" (#30): what an agent is doing before
	// its first token streams — set from agent_status events, cleared to
	// 'streaming' the moment real content starts arriving so the status
	// pill below hands off to the token-by-token cursor.
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

	// R-9's "roughly what kind of thing" — never renders tool args, only the
	// tool name the backend already limited itself to forwarding.
	function statusLabel(status: LiveStatus, detail: string | null): string {
		if (status === 'executing_tool') return detail ? `using ${detail}…` : 'using a tool…';
		if (status === 'waiting_for_handoff') return 'handing off…';
		return 'thinking…';
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
		rivulet?.title || messages.find((m) => m.sender_type === 'human')?.content || 'Rivulet'
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

	async function load(rivuletId: string, channelId: string) {
		loadError = null;
		try {
			const [loadedChannel, loadedRivulet, loadedMessages] = await Promise.all([
				channels.get(channelId),
				rivulets.get(rivuletId),
				rivulets.listMessages(rivuletId)
			]);
			channel = loadedChannel;
			rivulet = loadedRivulet;
			messages = loadedMessages;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load rivulet';
		}
	}

	$effect(() => {
		load(page.params.rivuletId!, page.params.id!);
	});

	$effect(() => {
		const rivuletId = page.params.rivuletId!;
		const token = auth.token;
		if (!token) return;

		// EventSource can't set an Authorization header, so the token goes
		// in the query string for this one endpoint only (api/deps.py's
		// get_current_workspace_id_for_stream) — everything else keeps
		// header-only auth.
		const source = new EventSource(
			`/api/v1/rivulets/${rivuletId}/stream?token=${encodeURIComponent(token)}`
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

		return () => source.close();
	});

	async function handleReply(event: SubmitEvent) {
		event.preventDefault();
		const rivuletId = page.params.rivuletId!;
		if (!reply.trim() && pendingFiles.length === 0) return;
		sending = true;
		try {
			// Uploads happen first, as their own step — a file has to exist on
			// the server (POST /files/upload) before it can be referenced by
			// file_id in the message body that attaches it.
			const uploaded = await Promise.all(pendingFiles.map((f) => filesApi.upload(f)));
			// The backend runs the dispatcher + any matched agent synchronously
			// before responding (dispatch/service.py), so re-fetching right
			// after this resolves already picks up an agent's reply — no
			// polling or SSE needed for the reply to show up.
			await rivulets.postMessage(
				rivuletId,
				reply.trim(),
				uploaded.map((f) => f.file_id)
			);
			reply = '';
			pendingFiles = [];
			messages = await rivulets.listMessages(rivuletId);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to send message';
		} finally {
			sending = false;
		}
	}

	async function handleResume() {
		const rivuletId = page.params.rivuletId!;
		resuming = true;
		try {
			rivulet = await rivulets.resume(rivuletId);
			messages = await rivulets.listMessages(rivuletId);
		} finally {
			resuming = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<header class="px-9 pt-6 pb-1">
		<a
			href={resolve('/channels/[id]', { id: page.params.id! })}
			class="text-xs text-neutral-500 hover:text-agent-cyan-600 dark:hover:text-agent-cyan-400"
		>
			&larr; {channel ? `#${channel.name}` : 'Back to channel'}
		</a>
		<div class="mt-2 mb-5 flex items-baseline gap-4">
			<h1 class="max-w-[60ch] truncate text-xl font-semibold text-ink dark:text-ink-dark">
				{title}
				{#if rivulet?.status && rivulet.status !== 'active'}
					<span class="ml-1 text-sm font-normal text-neutral-500">({rivulet.status})</span>
				{/if}
			</h1>
			{#if humanName || participants.length}
				<span class="ml-auto flex flex-none gap-1.5">
					{#if humanName}
						<span
							class="flex h-[22px] w-[22px] items-center justify-center rounded-sm text-[11px] font-semibold {HUMAN_AVATAR}"
							title={humanName}
						>
							{initials(humanName)}
						</span>
					{/if}
					{#each participants as p (p.name)}
						<span
							class="flex h-[22px] w-[22px] items-center justify-center rounded-sm text-[11px] font-semibold {INK_AVATAR[
								p.ink
							]}"
							title={p.name}
						>
							{initials(p.name)}
						</span>
					{/each}
				</span>
			{/if}
		</div>
	</header>

	{#if rivulet?.status === 'paused'}
		<div
			class="flex items-center justify-between gap-3 border-b border-agent-magenta-200 bg-agent-magenta-100/60 px-9 py-2 text-sm text-agent-magenta-800 dark:border-agent-magenta-900 dark:bg-agent-magenta-900/20 dark:text-agent-magenta-300"
		>
			<span>{pauseNotice ?? 'This rivulet is paused — agent replies are suppressed.'}</span>
			<button
				onclick={handleResume}
				disabled={resuming}
				class="shrink-0 rounded-md border border-agent-magenta-400 px-3 py-1 text-xs font-medium hover:bg-agent-magenta-100 disabled:opacity-50 dark:border-agent-magenta-700 dark:hover:bg-agent-magenta-900/30"
			>
				{resuming ? 'Resuming…' : 'Resume'}
			</button>
		</div>
	{/if}

	<div class="flex-1 overflow-y-auto px-9 py-4">
		{#if loadError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
		{:else}
			<div class="flex flex-col">
				{#each messages as message, i (message.id)}
					{#if message.content_type === 'handoff'}
						<div class="relative my-1 h-9">
							<svg
								class="absolute inset-0 h-full w-full text-agent-magenta-500"
								viewBox="0 0 900 36"
								preserveAspectRatio="none"
								aria-hidden="true"
							>
								<path
									d="M22 0 V36"
									fill="none"
									stroke="currentColor"
									class="spine-flowing"
									stroke-width="2"
									stroke-dasharray="5 4"
								/>
							</svg>
							<p class="relative pl-11 text-[12.5px] text-neutral-600 italic dark:text-neutral-400">
								<span
									class="bg-paper pr-2 font-medium text-agent-magenta-700 not-italic dark:bg-paper-dark dark:text-agent-magenta-400"
								>
									handoff
								</span>
								{message.content}
							</p>
						</div>
					{:else}
						{@const ink =
							message.sender_type === 'agent' && message.sender_id
								? inkMap.get(message.sender_id)
								: null}
						<div class="grid grid-cols-[44px_1fr]">
							<div class="flex flex-col items-center">
								<span
									class="flex h-[26px] w-[26px] flex-none items-center justify-center rounded-sm text-xs font-semibold {ink
										? INK_AVATAR[ink]
										: HUMAN_AVATAR}"
								>
									{initials(message.sender_name)}
								</span>
								{#if i < messages.length - 1}
									<span class="mt-1 w-0.5 flex-1 {ink ? INK_SPINE[ink] : HUMAN_SPINE}"></span>
								{/if}
							</div>
							<div class="pb-5">
								<div class="mb-0.5 text-[12.5px] text-neutral-600 dark:text-neutral-400">
									<strong class={ink ? INK_NAME_TEXT[ink] : HUMAN_NAME_TEXT}
										>{message.sender_name}</strong
									>
									· {formatClock(message.created_at)}
									{#if message.model_used}
										<span class="text-neutral-500" title="Auto mode ({message.tier} tier)">
											· via {message.model_used}
										</span>
									{/if}
								</div>
								{#if message.sender_type === 'system'}
									<p
										class="text-[12.5px] text-agent-magenta-700 italic dark:text-agent-magenta-400"
									>
										{message.content}
									</p>
								{:else}
									<p
										class="max-w-[68ch] text-[14.5px] leading-[1.55] whitespace-pre-wrap text-ink dark:text-ink-dark"
									>
										{message.content}
									</p>
								{/if}
								{#if message.attachments.length > 0}
									<div class="mt-1.5 flex flex-col gap-1">
										{#each message.attachments as attachment (attachment.file_id)}
											<button
												onclick={() => handleDownload(attachment)}
												class="flex w-fit items-center gap-1.5 text-left text-xs text-neutral-600 underline decoration-neutral-400 hover:text-agent-cyan-700 dark:text-neutral-400 dark:hover:text-agent-cyan-400"
											>
												<Icon name="paperclip" class="h-3 w-3" />
												{attachment.filename}
												<span class="text-neutral-500">({formatBytes(attachment.size_bytes)})</span>
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
					<div class="grid grid-cols-[44px_1fr]">
						<div class="flex flex-col items-center">
							<span
								class="flex h-[26px] w-[26px] flex-none items-center justify-center rounded-sm text-xs font-semibold {ink
									? INK_AVATAR[ink]
									: HUMAN_AVATAR}"
							>
								{initials(liveMessage.agentName)}
							</span>
						</div>
						<div class="pb-5">
							<div class="mb-0.5 text-[12.5px] text-neutral-600 dark:text-neutral-400">
								<strong class={ink ? INK_NAME_TEXT[ink] : HUMAN_NAME_TEXT}
									>{liveMessage.agentName}</strong
								>
							</div>
							{#if liveMessage.status === 'streaming' || liveMessage.content}
								<p
									class="max-w-[68ch] text-[14.5px] leading-[1.55] whitespace-pre-wrap text-ink dark:text-ink-dark"
								>
									{liveMessage.content}<span class="animate-pulse">▍</span>
								</p>
							{:else}
								<p
									class="flex items-center gap-1.5 text-[12.5px] text-neutral-500 italic dark:text-neutral-400"
								>
									{#if liveMessage.status === 'executing_tool'}
										<Icon name="wrench" class="h-3 w-3 flex-none" />
									{:else if liveMessage.status === 'waiting_for_handoff'}
										<Icon name="route" class="h-3 w-3 flex-none" />
									{:else}
										<span class="flex gap-0.5">
											<span
												class="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.3s]"
											></span>
											<span
												class="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.15s]"
											></span>
											<span class="h-1 w-1 animate-bounce rounded-full bg-current"></span>
										</span>
									{/if}
									{statusLabel(liveMessage.status, liveMessage.statusDetail)}
								</p>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<form
		onsubmit={handleReply}
		class="mx-9 mb-6 rounded-lg border border-ink/12 bg-surface px-3.5 py-2.5 shadow-sm dark:border-white/10 dark:bg-surface-dark"
	>
		{#if downloadError}
			<p class="mb-2 text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
				{downloadError}
			</p>
		{/if}
		{#if pendingFiles.length > 0}
			<div class="mb-2 flex flex-wrap gap-2">
				{#each pendingFiles as file, i (file.name + i)}
					<span
						class="flex items-center gap-1.5 rounded-md bg-neutral-200 px-2 py-1 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
					>
						<Icon name="paperclip" class="h-3.5 w-3.5" />
						{file.name}
						<button
							type="button"
							onclick={() => removePendingFile(i)}
							aria-label="Remove {file.name}"
							class="text-neutral-400 hover:text-agent-magenta-600"
						>
							&times;
						</button>
					</span>
				{/each}
			</div>
		{/if}
		<input
			type="text"
			bind:value={reply}
			placeholder="Reply to this rivulet…"
			class="w-full bg-transparent text-sm text-ink placeholder:text-neutral-500 focus:outline-none dark:text-ink-dark"
		/>
		<div class="mt-2.5 flex items-center gap-3.5">
			<input
				bind:this={fileInput}
				type="file"
				multiple
				onchange={handleFileSelect}
				class="hidden"
			/>
			<button
				type="button"
				onclick={() => fileInput?.click()}
				title="Attach files"
				aria-label="Attach files"
				class="text-neutral-600 hover:text-ink dark:text-neutral-400 dark:hover:text-ink-dark"
			>
				<Icon name="paperclip" class="h-[17px] w-[17px]" />
			</button>
			<span class="ml-auto font-mono text-[11.5px] text-neutral-500">⏎ send</span>
			<button
				type="submit"
				disabled={sending || (!reply.trim() && pendingFiles.length === 0)}
				class="rounded-md bg-agent-cyan px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-40"
			>
				{sending ? 'Sending…' : 'Send'}
			</button>
		</div>
	</form>
</div>
