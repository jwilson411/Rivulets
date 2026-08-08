<script lang="ts">
	// #15: accepting an invite is the one flow in this app that happens
	// before a browser session is authenticated at all -- +layout.svelte
	// special-cases this route to render outside the normal
	// LoginForm/IdentityPicker/app-shell gate (see its pathname check).
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { invites } from '$lib/api/invites';

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
			acceptError = err instanceof Error ? err.message : 'Failed to accept invite';
		} finally {
			accepting = false;
		}
	}
</script>

<main class="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-8 px-6">
	<header>
		<h1
			class="flex items-center gap-2 text-3xl font-semibold tracking-tight text-ink dark:text-ink-dark"
		>
			<img src="/logo.png" alt="" class="h-9 w-9" />
			Rivulets<span class="text-agent-cyan-600 dark:text-agent-cyan-400">.</span>
		</h1>
		<p class="mt-1 text-neutral-600 dark:text-neutral-400">
			You've been invited to join this workspace — no recovery phrase needed.
		</p>
	</header>

	<form onsubmit={handleAccept} class="flex flex-col gap-3">
		<label class="text-sm font-medium text-ink dark:text-ink-dark" for="display-name">
			Your name
		</label>
		<input
			id="display-name"
			type="text"
			bind:value={displayName}
			placeholder="e.g. Ada"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<button
			type="submit"
			disabled={accepting}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{accepting ? 'Joining…' : 'Join workspace'}
		</button>
		{#if acceptError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{acceptError}</p>
		{/if}
	</form>
</main>
