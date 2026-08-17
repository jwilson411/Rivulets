<script lang="ts">
	// Name screen (06-screens.md → Name, mockup 2a). #14: claims a Human
	// identity for this browser session, on top of the already-authenticated
	// workspace session (see +layout.svelte -- this renders once
	// auth.isAuthenticated but before auth.humanId is set). A lightweight
	// session claim, not a separate credential -- anyone holding the
	// workspace mnemonic can claim any identity here.
	import { onMount } from 'svelte';
	import { auth } from '$lib/api/auth.svelte';
	import { humans, type Human } from '$lib/api/humans';
	import { initials } from '$lib/ink';

	let existingHumans = $state<Human[]>([]);
	let loadError = $state<string | null>(null);
	let newName = $state('');
	let showNewName = $state(false);
	let claiming = $state(false);
	let claimError = $state<string | null>(null);

	onMount(async () => {
		try {
			existingHumans = await humans.list();
		} catch {
			loadError = "Couldn't load names. Try again.";
		}
	});

	async function continueAs(humanId: string) {
		claimError = null;
		claiming = true;
		try {
			await auth.claimIdentity({ humanId });
		} catch {
			claimError = "Couldn't continue with that name. Try again.";
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
		} catch {
			claimError = "Couldn't continue with that name. Try again.";
		} finally {
			claiming = false;
		}
	}
</script>

<main class="mx-auto flex min-h-screen w-full max-w-[480px] flex-col justify-center px-6 py-12">
	<h1 class="mb-3 font-display text-[32px] leading-tight font-semibold text-ink dark:text-ink-dark">
		What should we call you?
	</h1>
	<p class="mb-8 text-[15px] leading-normal text-muted dark:text-muted-dark">
		This name shows up on your messages. Anyone with the recovery phrase can use any name.
	</p>

	{#if loadError}
		<p class="mb-4 text-sm text-danger">{loadError}</p>
	{/if}

	{#if existingHumans.length > 0}
		<div class="mb-7 flex flex-col gap-3">
			{#each existingHumans as human (human.id)}
				<button
					type="button"
					disabled={claiming}
					onclick={() => continueAs(human.id)}
					class="flex h-16 w-full items-center gap-3.5 rounded-xl border border-line bg-surface px-4.5 text-left hover:border-accent disabled:opacity-50 dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<span
						class="flex h-8 w-8 flex-none items-center justify-center rounded-[12px] bg-ink text-sm font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
					>
						{initials(human.display_name)}
					</span>
					<span class="text-[17px] font-semibold text-ink dark:text-ink-dark">
						Continue as {human.display_name}
					</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if existingHumans.length === 0 || showNewName}
		<form onsubmit={claimNew} class="flex flex-col gap-3">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-identity-name">
				{existingHumans.length > 0 ? "I'm someone new" : 'Your name'}
			</label>
			<input
				id="new-identity-name"
				type="text"
				bind:value={newName}
				placeholder="e.g. Riley"
				class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
			/>
			<button
				type="submit"
				disabled={claiming || !newName.trim()}
				class="mt-1 flex h-12 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-white transition-colors hover:bg-accent-deep disabled:opacity-40 dark:bg-accent-dark dark:text-paper-dark"
			>
				{claiming ? 'Joining…' : 'Continue'}
			</button>
			{#if claimError}
				<p class="text-sm text-danger">{claimError}</p>
			{/if}
		</form>
	{:else}
		<button
			type="button"
			onclick={() => (showNewName = true)}
			class="self-start text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
		>
			I'm someone new
		</button>
		{#if claimError}
			<p class="mt-3 text-sm text-danger">{claimError}</p>
		{/if}
	{/if}
</main>
