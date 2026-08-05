<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { channels, type Channel } from '$lib/api/channels';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { teams, type Team } from '$lib/api/teams';
	import { files as filesApi } from '$lib/api/files';

	interface RivuletPreview {
		rootContent: string;
		messageCount: number;
		lastAgentMessage: Message | null;
	}

	let channel = $state<Channel | null>(null);
	let rivuletList = $state<Rivulet[]>([]);
	let previews = $state<Record<string, RivuletPreview>>({});
	let teamList = $state<Team[]>([]);
	let newMessage = $state('');
	let loadError = $state<string | null>(null);
	let posting = $state(false);
	let teamChangeError = $state<string | null>(null);
	let postError = $state<string | null>(null);
	let pendingFiles = $state<globalThis.File[]>([]);
	let fileInput = $state<HTMLInputElement | null>(null);

	function handleFileSelect(event: Event) {
		const input = event.target as HTMLInputElement;
		pendingFiles = [...pendingFiles, ...Array.from(input.files ?? [])];
		input.value = '';
	}

	function removePendingFile(index: number) {
		pendingFiles = pendingFiles.filter((_, i) => i !== index);
	}

	async function loadPreviews(list: Rivulet[]) {
		const entries = await Promise.all(
			list.map(async (t) => {
				const msgs = await rivulets.listMessages(t.id);
				const root = msgs.find((m) => m.sender_type === 'human');
				const lastAgent = [...msgs].reverse().find((m) => m.sender_type === 'agent') ?? null;
				const preview: RivuletPreview = {
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
			const [loadedChannel, loadedRivulets, loadedTeams] = await Promise.all([
				channels.get(channelId),
				rivulets.listForChannel(channelId),
				teams.list()
			]);
			channel = loadedChannel;
			rivuletList = loadedRivulets;
			teamList = loadedTeams;
			await loadPreviews(loadedRivulets);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load channel';
		}
	}

	$effect(() => {
		load(page.params.id!);
	});

	async function handleTeamChange(teamId: string) {
		const channelId = page.params.id!;
		teamChangeError = null;
		try {
			channel = await channels.update(channelId, { team_id: teamId || null });
		} catch (err) {
			teamChangeError = err instanceof Error ? err.message : 'Failed to change team';
		}
	}

	async function handlePost(event: SubmitEvent) {
		event.preventDefault();
		const channelId = page.params.id!;
		if (!newMessage.trim() && pendingFiles.length === 0) return;
		posting = true;
		postError = null;
		try {
			const uploaded = await Promise.all(pendingFiles.map((f) => filesApi.upload(f)));
			await rivulets.create(
				channelId,
				newMessage.trim(),
				uploaded.map((f) => f.file_id)
			);
			newMessage = '';
			pendingFiles = [];
			rivuletList = await rivulets.listForChannel(channelId);
			await loadPreviews(rivuletList);
		} catch (err) {
			postError = err instanceof Error ? err.message : 'Failed to send message';
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
			<div class="flex flex-col items-end gap-1">
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
				{#if teamChangeError}
					<p class="text-xs text-red-600 dark:text-red-400">{teamChangeError}</p>
				{/if}
			</div>
		{/if}
	</header>

	<div class="flex-1 overflow-y-auto px-6 py-4">
		{#if loadError}
			<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
		{:else if rivuletList.length === 0}
			<p class="text-sm text-zinc-400">No messages yet — say something below.</p>
		{:else}
			<ul class="flex flex-col gap-2">
				{#each rivuletList as rivulet (rivulet.id)}
					{@const preview = previews[rivulet.id]}
					<li>
						<a
							href={resolve('/channels/[id]/rivulets/[rivuletId]', {
								id: page.params.id!,
								rivuletId: rivulet.id
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

	<form
		onsubmit={handlePost}
		class="flex flex-col gap-2 border-t border-zinc-200 p-4 dark:border-zinc-800"
	>
		{#if postError}
			<p class="text-sm text-red-600 dark:text-red-400">{postError}</p>
		{/if}
		{#if pendingFiles.length > 0}
			<div class="flex flex-wrap gap-2">
				{#each pendingFiles as file, i (file.name + i)}
					<span
						class="flex items-center gap-1.5 rounded-md bg-zinc-100 px-2 py-1 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
					>
						📎 {file.name}
						<button
							type="button"
							onclick={() => removePendingFile(i)}
							aria-label="Remove {file.name}"
							class="text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
						>
							&times;
						</button>
					</span>
				{/each}
			</div>
		{/if}
		<div class="flex gap-2">
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
				class="rounded-md border border-zinc-300 px-3 py-2 text-sm text-zinc-500 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
			>
				📎
			</button>
			<input
				type="text"
				bind:value={newMessage}
				placeholder="Message #{channel?.name ?? ''}"
				class="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
			/>
			<button
				type="submit"
				disabled={posting || (!newMessage.trim() && pendingFiles.length === 0)}
				class="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
			>
				Send
			</button>
		</div>
	</form>
</div>
