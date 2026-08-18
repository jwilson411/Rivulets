<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { channels, type Channel } from '$lib/api/channels';
	import NewChannelSheet from '$lib/components/NewChannelSheet.svelte';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Channel list (04-information-architecture.md): on desktop the context
	// panel already shows channels, but this index is the mobile full-screen
	// push and the Hash rail target when no channel has been visited yet.
	// Archived channels hide behind a filter.

	let channelList = $state<Channel[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let listError = $state<string | null>(null);
	let showArchived = $state(false);
	let creating = $state(false);
	let archiving = $state<Channel | null>(null);
	let archiveBusy = $state(false);
	let archiveError = $state<string | null>(null);

	let visible = $derived(channelList.filter((c) => (showArchived ? c.archived : !c.archived)));

	async function load() {
		loading = true;
		loadError = null;
		try {
			channelList = await channels.list();
		} catch {
			loadError = "Couldn't load channels.";
		} finally {
			loading = false;
		}
	}

	load();

	function handleCreated(created: Channel) {
		creating = false;
		goto(resolve('/channels/[id]', { id: created.id }));
	}

	async function handleArchive() {
		if (!archiving) return;
		archiveBusy = true;
		archiveError = null;
		try {
			await channels.remove(archiving.id);
			channelList = channelList.map((c) => (c.id === archiving!.id ? { ...c, archived: true } : c));
			archiving = null;
		} catch {
			archiveError = "Couldn't archive that channel. Try again.";
		} finally {
			archiveBusy = false;
		}
	}

	async function handleUnarchive(channel: Channel) {
		listError = null;
		try {
			const updated = await channels.unarchive(channel.id);
			channelList = channelList.map((c) => (c.id === channel.id ? updated : c));
		} catch {
			listError = "Couldn't unarchive that channel. Try again.";
		}
	}
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-10">
	<div class="mb-5 flex items-center justify-between">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Channels</h1>
		<Button onclick={() => (creating = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New channel
		</Button>
	</div>

	<div class="mb-5 flex gap-2">
		<FilterChip selected={!showArchived} onclick={() => (showArchived = false)}>Active</FilterChip>
		<FilterChip selected={showArchived} onclick={() => (showArchived = true)}>Archived</FilterChip>
	</div>

	{#if listError}
		<ErrorBanner class="mb-4" message={listError} />
	{/if}
	{#if loading}
		<SkeletonCards count={3} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={load} />
	{:else if visible.length === 0}
		<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
			{showArchived ? 'No archived channels.' : 'No channels yet.'}
		</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each visible as channel (channel.id)}
				<div
					class="flex min-h-16 items-center gap-3 rounded-xl border border-line bg-surface px-4.5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<a
						href={resolve('/channels/[id]', { id: channel.id })}
						class="flex min-w-0 flex-1 items-center gap-3"
					>
						<span class="text-lg text-muted dark:text-muted-dark">#</span>
						<span class="truncate text-[17px] font-semibold text-ink dark:text-ink-dark">
							{channel.name}
						</span>
						{#if channel.description}
							<span class="ml-auto truncate text-sm text-muted dark:text-muted-dark">
								{channel.description}
							</span>
						{/if}
					</a>
					{#if channel.archived}
						<button
							type="button"
							onclick={() => handleUnarchive(channel)}
							class="flex-none text-sm font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
						>
							Unarchive
						</button>
					{:else}
						<button
							type="button"
							onclick={() => (archiving = channel)}
							class="flex-none text-sm font-semibold text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
						>
							Archive
						</button>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if creating}
	<NewChannelSheet onClose={() => (creating = false)} onCreated={handleCreated} />
{/if}

{#if archiving}
	<Sheet title="Archive this channel?" onClose={() => (archiving = null)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			It leaves the channel list. You can find it under Archived and restore it.
		</p>
		{#if archiveError}
			<p class="text-sm text-danger">{archiveError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (archiving = null)}>Cancel</Button>
			<Button variant="destructive" onclick={handleArchive} disabled={archiveBusy}>
				{archiveBusy ? 'Archiving…' : 'Archive'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
