<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { channels, type Channel } from '$lib/api/channels';
	import NewChannelSheet from '$lib/components/NewChannelSheet.svelte';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Channel list (04-information-architecture.md): on desktop the context
	// panel already shows channels, but this index is the mobile full-screen
	// push and the Hash rail target when no channel has been visited yet.
	// Archived channels hide behind a filter.

	let channelList = $state<Channel[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let showArchived = $state(false);
	let creating = $state(false);

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
				<a
					href={resolve('/channels/[id]', { id: channel.id })}
					class="flex min-h-16 items-center gap-3 rounded-xl border border-line bg-surface px-4.5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
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
			{/each}
		</div>
	{/if}
</div>

{#if creating}
	<NewChannelSheet onClose={() => (creating = false)} onCreated={handleCreated} />
{/if}
