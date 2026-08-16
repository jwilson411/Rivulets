<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { channels, type Channel } from '$lib/api/channels';
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
	let showArchived = $state(false);
	let creating = $state(false);
	let newName = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

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

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		const name = newName.trim();
		if (!name) return;
		createBusy = true;
		createError = null;
		try {
			const created = await channels.create(name);
			creating = false;
			newName = '';
			goto(resolve('/channels/[id]', { id: created.id }));
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the channel.";
		} finally {
			createBusy = false;
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
	<Sheet title="New channel" onClose={() => (creating = false)}>
		<form id="channels-new-form" onsubmit={handleCreate} class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="channels-new-name">
				Name
			</label>
			<input
				id="channels-new-name"
				type="text"
				bind:value={newName}
				placeholder="launch-readiness"
				class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
			/>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy || !newName.trim()}
				onclick={() =>
					(document.getElementById('channels-new-form') as HTMLFormElement).requestSubmit()}
			>
				Create channel
			</Button>
		{/snippet}
	</Sheet>
{/if}
