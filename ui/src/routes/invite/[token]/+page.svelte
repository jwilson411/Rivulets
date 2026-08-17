<script lang="ts">
	// Invite accept (06-screens.md → Invite accept, mockup 2b): same visual
	// language as Unlock, no app shell. #15: accepting an invite is the one
	// flow in this app that happens before a browser session is
	// authenticated at all -- +layout.svelte special-cases this route to
	// render outside the normal LoginForm/IdentityPicker/app-shell gate
	// (see its pathname check).
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { invites } from '$lib/api/invites';
	import Icon from '$lib/ui/Icon.svelte';

	let displayName = $state('');
	let accepting = $state(false);
	let acceptError = $state<string | null>(null);

	async function handleAccept(event: SubmitEvent) {
		event.preventDefault();
		acceptError = null;
		accepting = true;
		try {
			await invites.accept(page.params.token!, displayName.trim() || undefined);
			await goto(resolve('/'));
		} catch (err) {
			acceptError = err instanceof Error ? err.message : "Couldn't join. Try again.";
		} finally {
			accepting = false;
		}
	}
</script>

<main class="mx-auto flex min-h-screen w-full max-w-[480px] flex-col justify-center px-6 py-12">
	<div class="mb-10 flex items-center gap-3">
		<span
			class="flex h-9 w-9 items-center justify-center rounded-[12px] bg-accent text-white dark:bg-accent-dark dark:text-paper-dark"
		>
			<Icon name="logo" class="h-5 w-5" />
		</span>
		<span class="font-display text-[19px] font-semibold text-ink dark:text-ink-dark">Rivulets</span>
	</div>

	<h1 class="mb-3 font-display text-[32px] leading-tight font-semibold text-ink dark:text-ink-dark">
		You're invited.
	</h1>
	<p class="mb-8 text-base leading-normal text-muted dark:text-muted-dark">
		Join this workspace. You won't need a recovery phrase.
	</p>

	<form onsubmit={handleAccept} class="flex flex-col gap-3">
		<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="display-name">
			Your name
		</label>
		<input
			id="display-name"
			type="text"
			bind:value={displayName}
			placeholder="e.g. Ada"
			class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
		/>
		<button
			type="submit"
			disabled={accepting}
			class="mt-2 flex h-14 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-white transition-colors hover:bg-accent-deep disabled:opacity-40 dark:bg-accent-dark dark:text-paper-dark"
		>
			{accepting ? 'Joining…' : 'Join workspace'}
		</button>
		{#if acceptError}
			<p class="text-sm text-danger">{acceptError}</p>
		{/if}
	</form>
</main>
