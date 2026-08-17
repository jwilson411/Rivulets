<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { channels, type Channel } from '$lib/api/channels';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { teams, type Team } from '$lib/api/teams';
	import { providers, type Provider } from '$lib/api/providers';
	import { files as filesApi } from '$lib/api/files';
	import { formatClock } from '$lib/format';
	import { teamComposerHint } from '$lib/teamRouting';
	import { agentInkMap, INK_AVATAR, HUMAN_AVATAR } from '$lib/ink';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StreamBar from '$lib/ui/StreamBar.svelte';
	import SetupCards from '$lib/shell/SetupCards.svelte';

	// Home (06-screens.md): the missing screen. While setup is incomplete
	// (owner, no provider or no channel) it walks the first-value path;
	// once complete it's an inbox of recent conversations across channels
	// with a Stream Bar that asks which channel to post into.

	interface RecentConversation {
		rivulet: Rivulet;
		channel: Channel;
		title: string;
		preview: string;
		lastSender: string | null;
		lastSenderClass: string;
		lastAt: string | null;
	}

	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let channelList = $state<Channel[]>([]);
	let teamList = $state<Team[]>([]);
	let providerList = $state<Provider[]>([]);
	let recent = $state<RecentConversation[]>([]);
	let selectedChannelId = $state<string | null>(null);
	let sendError = $state<string | null>(null);
	let sending = $state(false);

	let isOwner = $derived(auth.grant === 'owner');
	let setupIncomplete = $derived(
		isOwner && (providerList.length === 0 || channelList.length === 0)
	);
	let activeChannels = $derived(channelList.filter((c) => !c.archived));
	let selectedChannel = $derived(
		activeChannels.find((c) => c.id === selectedChannelId) ?? activeChannels[0] ?? null
	);
	let helper = $derived.by(() => {
		if (!selectedChannel) return null;
		const team = teamList.find((t) => t.id === selectedChannel.team_id);
		return team ? teamComposerHint(team.name) : 'No team on this channel — add one';
	});
	let generalChannel = $derived(
		activeChannels.find((c) => c.name === 'general') ?? activeChannels[0] ?? null
	);

	async function load() {
		loading = true;
		loadError = null;
		try {
			const [loadedChannels, loadedTeams, loadedProviders] = await Promise.all([
				channels.list(),
				teams.list().catch(() => []),
				isOwner ? providers.list().catch(() => [] as Provider[]) : Promise.resolve([])
			]);
			channelList = loadedChannels;
			teamList = loadedTeams;
			providerList = loadedProviders;
			await loadRecent();
		} catch {
			loadError = "Couldn't load conversations.";
		} finally {
			loading = false;
		}
	}

	async function loadRecent() {
		const open = channelList.filter((c) => !c.archived);
		const perChannel = await Promise.all(
			open.map(async (channel) => {
				const list = await rivulets.listForChannel(channel.id).catch(() => [] as Rivulet[]);
				return list.map((rivulet) => ({ rivulet, channel }));
			})
		);
		const all = perChannel
			.flat()
			.sort((a, b) => b.rivulet.created_at.localeCompare(a.rivulet.created_at))
			.slice(0, 8);
		recent = await Promise.all(
			all.map(async ({ rivulet, channel }) => {
				const msgs = await rivulets.listMessages(rivulet.id).catch(() => [] as Message[]);
				const spoken = msgs.filter((m) => m.content_type === 'text');
				const root = spoken.find((m) => m.sender_type === 'human');
				const last = spoken.at(-1) ?? null;
				const inkMap = agentInkMap(msgs);
				const ink =
					last?.sender_type === 'agent' && last.sender_id ? inkMap.get(last.sender_id) : null;
				return {
					rivulet,
					channel,
					title: rivulet.title || root?.content || 'Conversation',
					preview: last && last !== root ? last.content : '',
					lastSender: last?.sender_name ?? null,
					lastSenderClass: ink ? INK_AVATAR[ink] : HUMAN_AVATAR,
					lastAt: last?.created_at ?? rivulet.created_at
				};
			})
		);
	}

	load();

	async function handleSend(text: string, files: File[]): Promise<boolean> {
		if (!selectedChannel) return false;
		sending = true;
		sendError = null;
		try {
			const uploaded = await Promise.all(files.map((f) => filesApi.upload(f)));
			const created = await rivulets.create(
				selectedChannel.id,
				text,
				uploaded.map((f) => f.file_id)
			);
			goto(
				resolve('/channels/[id]/rivulets/[rivuletId]', {
					id: selectedChannel.id,
					rivuletId: created.id
				})
			);
			return true;
		} catch {
			sendError = "Couldn't send that. Try again.";
			return false;
		} finally {
			sending = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<div class="flex items-baseline justify-between px-4 pt-8 md:px-10">
		<h1 class="font-display text-[32px] font-semibold text-ink dark:text-ink-dark">Home</h1>
		<span class="text-sm text-muted dark:text-muted-dark">
			{auth.displayName} · {isOwner ? 'Owner' : 'Guest'}
		</span>
	</div>

	{#if setupIncomplete}
		<div class="mx-auto w-full max-w-[640px] flex-1 overflow-y-auto px-4 pt-6 pb-10 md:px-0">
			<h2 class="mb-2 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">
				Three things before the team can answer
			</h2>
			<p class="mb-8 text-base text-muted dark:text-muted-dark">You only do this once.</p>
			<SetupCards {providerList} {channelList} onProviderAdded={load} />
		</div>
	{:else}
		<SectionLabel class="px-4 pt-6 text-sm md:px-10">Recent conversations</SectionLabel>
		<div class="flex-1 overflow-y-auto px-4 py-4 md:px-10">
			{#if loading}
				<SkeletonCards count={2} />
			{:else if loadError}
				<ErrorBanner message={loadError} onRetry={load} />
			{:else if recent.length === 0}
				<div class="py-6 text-center">
					<p class="mb-4 text-base text-muted dark:text-muted-dark">No conversations yet.</p>
					{#if generalChannel}
						<Button onclick={() => goto(resolve('/channels/[id]', { id: generalChannel!.id }))}>
							Start one in #{generalChannel.name}
						</Button>
					{/if}
				</div>
			{:else}
				<div class="flex flex-col gap-4">
					{#each recent as item, i (item.rivulet.id)}
						<a
							href={resolve('/channels/[id]/rivulets/[rivuletId]', {
								id: item.channel.id,
								rivuletId: item.rivulet.id
							})}
							class="flex min-h-[88px] gap-4 rounded-2xl border border-line bg-surface px-6 py-5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
						>
							<span
								class="mt-2 h-2 w-2 flex-none rounded-full {item.rivulet.status === 'paused'
									? 'bg-warn'
									: i === 0
										? 'breath bg-accent dark:bg-accent-dark'
										: 'bg-line dark:bg-line-dark'}"
							></span>
							<span class="min-w-0">
								<span
									class="mb-1 block text-sm font-semibold {i === 0
										? 'text-accent dark:text-accent-dark'
										: 'text-muted dark:text-muted-dark'}"
								>
									#{item.channel.name}
								</span>
								<span
									class="mb-1 block truncate text-base leading-snug font-semibold text-ink dark:text-ink-dark"
								>
									{item.title}
								</span>
								{#if item.preview}
									<span
										class="block truncate text-[15px] leading-snug text-muted dark:text-muted-dark"
									>
										{item.preview}
									</span>
								{/if}
								{#if item.lastSender}
									<span
										class="mt-2 flex items-center gap-2 text-[13px] text-muted dark:text-muted-dark"
									>
										<Disc name={item.lastSender} colorClass={item.lastSenderClass} size={20} />
										{item.lastSender}{item.lastAt ? ` · ${formatClock(item.lastAt)}` : ''}
									</span>
								{/if}
							</span>
						</a>
					{/each}
				</div>
			{/if}
		</div>

		<div class="px-4 pb-24 md:px-10 md:pb-7">
			{#if activeChannels.length > 0}
				<div class="mb-3 flex flex-wrap gap-2">
					{#each activeChannels as channel (channel.id)}
						{@const selected = selectedChannel?.id === channel.id}
						<button
							type="button"
							onclick={() => (selectedChannelId = channel.id)}
							aria-pressed={selected}
							class="inline-flex h-8 items-center rounded-full px-3.5 text-sm {selected
								? 'border border-accent bg-accent-soft font-semibold text-accent dark:border-accent-dark dark:bg-accent-soft-dark dark:text-accent-dark'
								: 'border border-line bg-surface font-medium text-muted dark:border-line-dark dark:bg-surface-dark dark:text-muted-dark'}"
						>
							#{channel.name}
						</button>
					{/each}
				</div>
			{/if}
			<StreamBar
				placeholder="Start a conversation…"
				{helper}
				busy={sending}
				error={sendError}
				onSend={handleSend}
			/>
		</div>
	{/if}
</div>
