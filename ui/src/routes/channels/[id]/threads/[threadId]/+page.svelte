<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { threads, type Thread, type Message } from '$lib/api/threads';
	import { auth } from '$lib/api/auth.svelte';

	let thread = $state<Thread | null>(null);
	let messages = $state<Message[]>([]);
	let reply = $state('');
	let loadError = $state<string | null>(null);
	let sending = $state(false);
	let resuming = $state(false);

	// Filled in token-by-token from the SSE stream below (FR-12.3) while an
	// agent is mid-run, then cleared once its agent_message event lands —
	// at which point the message is already in `messages` too, either from
	// this same event or from the refetch handleReply does once its POST
	// resolves (dispatch runs synchronously server-side; SSE is what makes
	// the wait visible incrementally instead of as one silent pause).
	let liveMessage = $state<{ agentId: string; agentName: string; content: string } | null>(null);

	let pauseNotice = $derived(
		[...messages].reverse().find((m) => m.content_type === 'system_alert')?.content ?? null
	);

	async function load(threadId: string) {
		loadError = null;
		try {
			const [loadedThread, loadedMessages] = await Promise.all([
				threads.get(threadId),
				threads.listMessages(threadId)
			]);
			thread = loadedThread;
			messages = loadedMessages;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load thread';
		}
	}

	$effect(() => {
		load(page.params.threadId!);
	});

	$effect(() => {
		const threadId = page.params.threadId!;
		const token = auth.token;
		if (!token) return;

		// EventSource can't set an Authorization header, so the token goes
		// in the query string for this one endpoint only (api/deps.py's
		// get_current_workspace_id_for_stream) — everything else keeps
		// header-only auth.
		const source = new EventSource(
			`/api/v1/threads/${threadId}/stream?token=${encodeURIComponent(token)}`
		);

		source.addEventListener('agent_token', (event) => {
			const data = JSON.parse((event as MessageEvent).data);
			if (!liveMessage || liveMessage.agentId !== data.agent_id) {
				liveMessage = { agentId: data.agent_id, agentName: data.agent_name, content: '' };
			}
			liveMessage.content += data.token;
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
		const threadId = page.params.threadId!;
		if (!reply.trim()) return;
		sending = true;
		try {
			// The backend runs the dispatcher + any matched agent synchronously
			// before responding (dispatch/service.py), so re-fetching right
			// after this resolves already picks up an agent's reply — no
			// polling or SSE needed for the reply to show up.
			await threads.postMessage(threadId, reply.trim());
			reply = '';
			messages = await threads.listMessages(threadId);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to send message';
		} finally {
			sending = false;
		}
	}

	async function handleResume() {
		const threadId = page.params.threadId!;
		resuming = true;
		try {
			thread = await threads.resume(threadId);
			messages = await threads.listMessages(threadId);
		} finally {
			resuming = false;
		}
	}

	function bubbleClass(senderType: Message['sender_type']): string {
		if (senderType === 'human') {
			return 'self-end bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900';
		}
		if (senderType === 'agent') {
			return 'self-start bg-blue-50 text-zinc-900 dark:bg-blue-950 dark:text-zinc-100';
		}
		return 'self-center bg-amber-50 text-xs text-amber-900 italic dark:bg-amber-950 dark:text-amber-200';
	}
</script>

<div class="flex h-full flex-col">
	<header class="border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
		<a
			href={resolve('/channels/[id]', { id: page.params.id! })}
			class="text-xs text-zinc-500 hover:underline"
		>
			&larr; Back to channel
		</a>
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
			Thread{thread?.status && thread.status !== 'active' ? ` (${thread.status})` : ''}
		</h1>
	</header>

	{#if thread?.status === 'paused'}
		<div
			class="flex items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-6 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
		>
			<span>{pauseNotice ?? 'This thread is paused — agent replies are suppressed.'}</span>
			<button
				onclick={handleResume}
				disabled={resuming}
				class="shrink-0 rounded-md border border-amber-300 px-3 py-1 text-xs font-medium hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:hover:bg-amber-900"
			>
				{resuming ? 'Resuming…' : 'Resume'}
			</button>
		</div>
	{/if}

	<div class="flex flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
		{#if loadError}
			<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
		{:else}
			{#each messages as message (message.id)}
				{#if message.content_type === 'handoff'}
					<!-- FR-6.3: a distinct divider, not a chat bubble — this didn't
					come from either party "saying" something, it's an event. -->
					<div class="my-1 flex items-center gap-3 self-stretch">
						<div class="h-px flex-1 bg-violet-200 dark:bg-violet-800"></div>
						<span
							class="rounded-full bg-violet-100 px-3 py-1 text-xs font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300"
						>
							{message.content}
						</span>
						<div class="h-px flex-1 bg-violet-200 dark:bg-violet-800"></div>
					</div>
				{:else}
					<div
						class="flex max-w-lg flex-col gap-1 rounded-lg px-4 py-2 {bubbleClass(
							message.sender_type
						)}"
					>
						<p class="text-xs font-medium opacity-70">{message.sender_name}</p>
						<p class="text-sm whitespace-pre-wrap">{message.content}</p>
					</div>
				{/if}
			{/each}
			{#if liveMessage}
				<div class="flex max-w-lg flex-col gap-1 rounded-lg bg-blue-50 px-4 py-2 dark:bg-blue-950">
					<p class="text-xs font-medium text-zinc-900 opacity-70 dark:text-zinc-100">
						{liveMessage.agentName}
					</p>
					<p class="text-sm whitespace-pre-wrap text-zinc-900 dark:text-zinc-100">
						{liveMessage.content}<span class="animate-pulse">▍</span>
					</p>
				</div>
			{/if}
		{/if}
	</div>

	<form onsubmit={handleReply} class="flex gap-2 border-t border-zinc-200 p-4 dark:border-zinc-800">
		<input
			type="text"
			bind:value={reply}
			placeholder="Reply…"
			class="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={sending || !reply.trim()}
			class="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			{sending ? 'Sending…' : 'Reply'}
		</button>
	</form>
</div>
