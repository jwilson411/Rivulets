<script lang="ts">
	// #14: claims a Human identity for this browser session, on top of the
	// already-authenticated workspace session (see +layout.svelte -- this
	// renders once auth.isAuthenticated but before auth.humanId is set).
	// A lightweight session claim, not a separate credential -- anyone
	// holding the workspace mnemonic can claim any identity here.
	import { onMount } from 'svelte';
	import { auth } from '$lib/api/auth.svelte';
	import { humans, type Human } from '$lib/api/humans';

	let existingHumans = $state<Human[]>([]);
	let loadError = $state<string | null>(null);
	let newName = $state('');
	let claiming = $state(false);
	let claimError = $state<string | null>(null);

	onMount(async () => {
		try {
			existingHumans = await humans.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load identities';
		}
	});

	async function continueAs(humanId: string) {
		claimError = null;
		claiming = true;
		try {
			await auth.claimIdentity({ humanId });
		} catch (err) {
			claimError = err instanceof Error ? err.message : 'Failed to claim identity';
		} finally {
			claiming = false;
		}
	}

	async function claimNew(event: SubmitEvent) {
		event.preventDefault();
		claimError = null;
		claiming = true;
		try {
			await auth.claimIdentity({ displayName: newName.trim() });
			newName = '';
		} catch (err) {
			claimError = err instanceof Error ? err.message : 'Failed to claim identity';
		} finally {
			claiming = false;
		}
	}
</script>

<main class="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
	<header>
		<h1 class="text-2xl font-semibold tracking-tight text-ink dark:text-ink-dark">Who's this?</h1>
		<p class="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
			Pick an identity for this session — anyone with the workspace phrase can claim any name.
		</p>
	</header>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{/if}

	{#if existingHumans.length > 0}
		<div class="flex flex-col gap-2">
			{#each existingHumans as human (human.id)}
				<button
					type="button"
					disabled={claiming}
					onclick={() => continueAs(human.id)}
					class="rounded-md border border-ink/15 px-4 py-2 text-left text-sm font-medium text-ink transition-colors hover:border-agent-cyan-600 disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
				>
					Continue as {human.display_name}
				</button>
			{/each}
		</div>
	{/if}

	<form onsubmit={claimNew} class="flex flex-col gap-3">
		<label class="text-sm font-medium text-ink dark:text-ink-dark" for="new-identity-name">
			{existingHumans.length > 0 ? "Or, I'm someone new" : 'Your name'}
		</label>
		<input
			id="new-identity-name"
			type="text"
			bind:value={newName}
			placeholder="e.g. Ada"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<button
			type="submit"
			disabled={claiming || !newName.trim()}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{claiming ? 'Joining…' : 'Continue'}
		</button>
		{#if claimError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{claimError}</p>
		{/if}
	</form>
</main>
