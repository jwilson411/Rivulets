<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { threads, type Thread, type Message } from '$lib/api/threads';

	let thread = $state<Thread | null>(null);
	let messages = $state<Message[]>([]);
	let reply = $state('');
	let loadError = $state<string | null>(null);
	let sending = $state(false);

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

	<div class="flex flex-1 flex-col gap-3 overflow-y-auto px-6 py-4">
		{#if loadError}
			<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
		{:else}
			{#each messages as message (message.id)}
				<div
					class="flex max-w-lg flex-col gap-1 rounded-lg px-4 py-2 {bubbleClass(
						message.sender_type
					)}"
				>
					<p class="text-xs font-medium opacity-70">{message.sender_name}</p>
					<p class="text-sm whitespace-pre-wrap">{message.content}</p>
				</div>
			{/each}
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
