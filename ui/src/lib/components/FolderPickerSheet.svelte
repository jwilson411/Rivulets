<script lang="ts">
	import { settings, type DirectoryListing } from '$lib/api/settings';
	import Button from '$lib/ui/Button.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';

	// Owner-only folder picker for the agent working directory. Lists
	// directories on this machine via the App Server — a browser file
	// input can't hand back an absolute path the tools can use.

	let {
		initialPath = null,
		onClose,
		onSelect
	}: {
		initialPath?: string | null;
		onClose: () => void;
		onSelect: (path: string) => void;
	} = $props();

	let listing = $state<DirectoryListing | null>(null);
	let loadError = $state<string | null>(null);
	let jumpPath = $state('');
	let creating = $state(false);
	let newFolderName = $state('');
	let createError = $state<string | null>(null);
	let creatingBusy = $state(false);

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';

	async function load(path?: string | null) {
		loadError = null;
		try {
			listing = await settings.listDirectories(path ?? undefined);
			jumpPath = listing.path;
		} catch {
			loadError = "Couldn't open that folder.";
		}
	}

	$effect(() => {
		void load(initialPath);
	});

	async function handleJump(event: SubmitEvent) {
		event.preventDefault();
		if (!jumpPath.trim()) return;
		await load(jumpPath.trim());
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!listing || !newFolderName.trim()) return;
		creatingBusy = true;
		createError = null;
		try {
			listing = await settings.createDirectory(listing.path, newFolderName.trim());
			jumpPath = listing.path;
			newFolderName = '';
			creating = false;
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create that folder.";
		} finally {
			creatingBusy = false;
		}
	}
</script>

<Sheet title="Choose a folder" {onClose} width={560}>
	<div class="flex flex-col gap-4">
		<p class="text-sm leading-normal text-muted dark:text-muted-dark">
			Agents will read and write files here. Pick a project folder on this machine.
		</p>
		<form onsubmit={handleJump} class="flex gap-2">
			<input
				type="text"
				bind:value={jumpPath}
				aria-label="Folder path"
				spellcheck="false"
				class="{inputClass} min-w-0 flex-1 font-mono text-[13px]"
			/>
			<Button type="submit" variant="secondary" size="md">Go</Button>
		</form>
		<div class="flex items-center gap-2">
			<button
				type="button"
				disabled={!listing?.parent}
				onclick={() => listing?.parent && load(listing.parent)}
				class="flex h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-accent disabled:pointer-events-none disabled:opacity-40 dark:text-accent-dark"
			>
				<Icon name="back" class="h-4 w-4" />
				Up
			</button>
			<button
				type="button"
				onclick={() => {
					creating = !creating;
					createError = null;
				}}
				class="flex h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-accent dark:text-accent-dark"
			>
				<Icon name="plus" class="h-4 w-4" />
				New folder
			</button>
		</div>
		{#if creating}
			<form onsubmit={handleCreate} class="flex flex-col gap-2">
				<input
					type="text"
					bind:value={newFolderName}
					aria-label="New folder name"
					placeholder="Folder name"
					class={inputClass}
				/>
				<div class="flex items-center gap-2">
					<Button type="submit" size="md" disabled={creatingBusy || !newFolderName.trim()}>
						{creatingBusy ? 'Creating…' : 'Create'}
					</Button>
					<Button
						type="button"
						variant="secondary"
						size="md"
						onclick={() => {
							creating = false;
							newFolderName = '';
						}}
					>
						Cancel
					</Button>
				</div>
				{#if createError}
					<p class="text-sm text-danger">{createError}</p>
				{/if}
			</form>
		{/if}
		{#if loadError}
			<p class="text-sm text-danger">{loadError}</p>
		{:else if !listing}
			<div class="breath h-4 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
		{:else if listing.entries.length === 0}
			<p class="py-6 text-center text-sm text-muted dark:text-muted-dark">
				No folders here. Use this one, or create a new folder.
			</p>
		{:else}
			<ul class="max-h-72 overflow-y-auto rounded-xl border border-line dark:border-line-dark">
				{#each listing.entries as entry (entry.path)}
					<li class="border-b border-line last:border-b-0 dark:border-line-dark">
						<button
							type="button"
							onclick={() => load(entry.path)}
							class="flex min-h-12 w-full items-center gap-3 px-4 text-left text-[15px] text-ink hover:bg-paper dark:text-ink-dark dark:hover:bg-paper-dark"
						>
							<Icon name="folder" class="h-4 w-4 flex-none text-muted dark:text-muted-dark" />
							<span class="min-w-0 truncate">{entry.name}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
	{#snippet footer()}
		<Button variant="secondary" onclick={onClose}>Cancel</Button>
		<Button disabled={!listing} onclick={() => listing && onSelect(listing.path)}>
			Use this folder
		</Button>
	{/snippet}
</Sheet>
