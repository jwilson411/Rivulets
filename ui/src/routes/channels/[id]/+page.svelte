<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { channels, type Channel } from '$lib/api/channels';
	import { threads, type Thread, type Message } from '$lib/api/threads';
	import { teams, type Team } from '$lib/api/teams';

	interface ThreadPreview {
		rootContent: string;
		messageCount: number;
		lastAgentMessage: Message | null;
	}

	let channel = $state<Channel | null>(null);
	let threadList = $state<Thread[]>([]);
	let previews = $state<Record<string, ThreadPreview>>({});
	let teamList = $state<Team[]>([]);
	let newMessage = $state('');
	let loadError = $state<string | null>(null);
	let posting = $state(false);

	async function loadPreviews(list: Thread[]) {
		const entries = await Promise.all(
			list.map(async (t) => {
				const msgs = await threads.listMessages(t.id);
				const root = msgs.find((m) => m.sender_type === 'human');
				const lastAgent = [...msgs].reverse().find((m) => m.sender_type === 'agent') ?? null;
				const preview: ThreadPreview = {
					rootContent: root?.content ?? '',
					messageCount: msgs.length,
					lastAgentMessage: lastAgent
				};
				return [t.id, preview] as const;
			})
		);
		previews = Object.fromEntries(entries);
	}

	async function load(channelId: string) {
		loadError = null;
		try {
			const [loadedChannel, loadedThreads, loadedTeams] = await Promise.all([
				channels.get(channelId),
				threads.listForChannel(channelId),
				teams.list()
			]);
			channel = loadedChannel;
			threadList = loadedThreads;
			teamList = loadedTeams;
			await loadPreviews(loadedThreads);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load channel';
		}
	}

	$effect(() => {
		load(page.params.id!);
	});

	async function handleTeamChange(teamId: string) {
		const channelId = page.params.id!;
		channel = await channels.update(channelId, { team_id: teamId || null });
	}

	async function handlePost(event: SubmitEvent) {
		event.preventDefault();
		const channelId = page.params.id!;
		if (!newMessage.trim()) return;
		posting = true;
		try {
			await threads.create(channelId, newMessage.trim());
			newMessage = '';
			threadList = await threads.listForChannel(channelId);
			await loadPreviews(threadList);
		} finally {
			posting = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<header
		class="flex items-center justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-800"
	>
		<div>
			<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
				#{channel?.name ?? '…'}
			</h1>
			{#if channel?.description}
				<p class="text-sm text-zinc-500">{channel.description}</p>
			{/if}
		</div>
		{#if channel}
			<label class="flex items-center gap-2 text-xs text-zinc-500">
				Team:
				<select
					value={channel.team_id ?? ''}
					onchange={(e) => handleTeamChange((e.target as HTMLSelectElement).value)}
					class="rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
				>
					<option value="">No team</option>
					{#each teamList as team (team.id)}
						<option value={team.id}>{team.name}</option>
					{/each}
				</select>
			</label>
		{/if}
	</header>

	<div class="flex-1 overflow-y-auto px-6 py-4">
		{#if loadError}
			<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
		{:else if threadList.length === 0}
			<p class="text-sm text-zinc-400">No messages yet — say something below.</p>
		{:else}
			<ul class="flex flex-col gap-2">
				{#each threadList as thread (thread.id)}
					{@const preview = previews[thread.id]}
					<li>
						<a
							href={resolve('/channels/[id]/threads/[threadId]', {
								id: page.params.id!,
								threadId: thread.id
							})}
							class="block rounded-md border border-zinc-200 px-4 py-3 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900"
						>
							<p class="truncate text-sm text-zinc-900 dark:text-zinc-100">
								{preview?.rootContent ?? '…'}
							</p>
							{#if preview?.lastAgentMessage}
								<p class="mt-1 truncate text-xs text-zinc-500">
									<span class="font-medium">{preview.lastAgentMessage.sender_name}</span> replied
									{#if preview.messageCount > 2}
										· {preview.messageCount - 1} replies
									{/if}
								</p>
							{/if}
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	</div>

	<form onsubmit={handlePost} class="flex gap-2 border-t border-zinc-200 p-4 dark:border-zinc-800">
		<input
			type="text"
			bind:value={newMessage}
			placeholder="Message #{channel?.name ?? ''}"
			class="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={posting || !newMessage.trim()}
			class="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			Send
		</button>
	</form>
</div>
