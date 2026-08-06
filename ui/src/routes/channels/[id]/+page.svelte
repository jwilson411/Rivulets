<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { channels, type Channel } from '$lib/api/channels';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { teams, type Team } from '$lib/api/teams';
	import { files as filesApi } from '$lib/api/files';
	import { agentInkMap, INK_SWATCH, INK_NAME_TEXT, type AgentInk } from '$lib/ink';
	import { timeAgo } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';

	interface RivuletPreview {
		rootContent: string;
		messageCount: number;
		lastAgentMessage: Message | null;
		lastAgentInk: AgentInk | null;
		agentInks: AgentInk[];
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

	let routedTeamName = $derived(teamList.find((t) => t.id === channel?.team_id)?.name ?? null);

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
				const inkMap = agentInkMap(msgs);
				const agentInks = [...new Set(inkMap.values())];
				const lastAgentInk =
					lastAgent?.sender_id && inkMap.has(lastAgent.sender_id)
						? (inkMap.get(lastAgent.sender_id) ?? null)
						: null;
				const preview: RivuletPreview = {
					rootContent: root?.content ?? '',
					messageCount: msgs.length,
					lastAgentMessage: lastAgent,
					lastAgentInk,
					agentInks
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
	<header class="flex items-baseline gap-4 px-9 pt-6 pb-4">
		<h1 class="text-[26px] font-semibold text-ink dark:text-ink-dark">
			<span class="text-neutral-400 dark:text-neutral-600">#</span>{channel?.name ?? '…'}
		</h1>
		<span class="text-[12.5px] text-neutral-600 dark:text-neutral-400">
			{rivuletList.length} rivulet{rivuletList.length === 1 ? '' : 's'}
		</span>
		{#if channel?.description}
			<span class="text-[12.5px] text-neutral-500">· {channel.description}</span>
		{/if}
		<div class="ml-auto flex flex-col items-end gap-1">
			<label class="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
				<Icon name="users" class="h-[15px] w-[15px] text-agent-cyan-600 dark:text-agent-cyan-400" />
				<select
					value={channel?.team_id ?? ''}
					onchange={(e) => handleTeamChange((e.target as HTMLSelectElement).value)}
					class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
				>
					<option value="">No team</option>
					{#each teamList as team (team.id)}
						<option value={team.id}>{team.name}</option>
					{/each}
				</select>
			</label>
			{#if teamChangeError}
				<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
					{teamChangeError}
				</p>
			{/if}
		</div>
	</header>

	<div class="flex-1 overflow-y-auto px-9 pb-4">
		{#if loadError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
		{:else if rivuletList.length === 0}
			<p class="text-sm text-neutral-500 italic">No rivulets yet — start one below.</p>
		{:else}
			<ul class="flex flex-col gap-6">
				{#each rivuletList as rivulet (rivulet.id)}
					{@const preview = previews[rivulet.id]}
					<li>
						<a
							href={resolve('/channels/[id]/rivulets/[rivuletId]', {
								id: page.params.id!,
								rivuletId: rivulet.id
							})}
							class="group grid grid-cols-[10px_1fr] items-start gap-3.5"
						>
							<span class="mt-1.5 flex flex-col items-center gap-1">
								{#if preview?.agentInks.length}
									{#each preview.agentInks as ink (ink)}
										<span class="h-2 w-2 rounded-sm {INK_SWATCH[ink]}"></span>
									{/each}
								{:else}
									<span class="h-2 w-2 rounded-sm bg-ink dark:bg-ink-dark"></span>
								{/if}
							</span>
							<div>
								<div
									class="mb-0.5 truncate text-[15.5px] font-semibold text-ink group-hover:underline dark:text-ink-dark"
								>
									{preview?.rootContent || '…'}
								</div>
								{#if preview?.lastAgentMessage}
									<p
										class="max-w-[68ch] truncate text-[13px] text-neutral-700 dark:text-neutral-300"
									>
										<strong
											class={preview.lastAgentInk
												? INK_NAME_TEXT[preview.lastAgentInk]
												: 'text-ink dark:text-ink-dark'}
										>
											{preview.lastAgentMessage.sender_name}
										</strong>
										— {preview.lastAgentMessage.content}
									</p>
								{/if}
								<p class="mt-1 text-xs text-neutral-500">
									{preview?.messageCount ?? 0} message{(preview?.messageCount ?? 0) === 1
										? ''
										: 's'} · {timeAgo(rivulet.created_at)}
								</p>
							</div>
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	</div>

	<footer class="px-9 pt-2 pb-6">
		<form
			onsubmit={handlePost}
			class="rounded-lg border border-ink/12 bg-surface px-3.5 py-2.5 shadow-sm dark:border-white/10 dark:bg-surface-dark"
		>
			{#if postError}
				<p class="mb-2 text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{postError}</p>
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
				bind:value={newMessage}
				placeholder="Start a rivulet in #{channel?.name ?? ''}…"
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
				{#if routedTeamName}
					<span
						class="flex items-center gap-1.5 text-[12.5px] text-neutral-600 dark:text-neutral-400"
					>
						<Icon
							name="route"
							class="h-[15px] w-[15px] text-agent-cyan-600 dark:text-agent-cyan-400"
						/>
						routes to
						<strong class="text-agent-cyan-700 dark:text-agent-cyan-400">{routedTeamName}</strong>
					</span>
				{/if}
				<span class="ml-auto font-mono text-[11.5px] text-neutral-500">⏎ send</span>
				<button
					type="submit"
					disabled={posting || (!newMessage.trim() && pendingFiles.length === 0)}
					class="rounded-md bg-agent-cyan px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-40"
				>
					{posting ? 'Sending…' : 'Send'}
				</button>
			</div>
		</form>
	</footer>
</div>
