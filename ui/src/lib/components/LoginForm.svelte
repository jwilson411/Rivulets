<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';

	let mnemonic = $state('');
	let loginError = $state<string | null>(null);
	let loggingIn = $state(false);

	async function handleLogin(event: SubmitEvent) {
		event.preventDefault();
		loginError = null;
		loggingIn = true;
		try {
			await auth.login(mnemonic.trim());
			mnemonic = '';
		} catch (err) {
			loginError = err instanceof Error ? err.message : 'Login failed';
		} finally {
			loggingIn = false;
		}
	}
</script>

<main class="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-8 px-6">
	<header>
		<h1 class="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">Rivulets</h1>
		<p class="mt-1 text-zinc-500 dark:text-zinc-400">
			A local-first workspace where conversations run like small streams — humans and AI agent
			teams, side by side.
		</p>
	</header>

	<form onsubmit={handleLogin} class="flex flex-col gap-3">
		<label class="text-sm font-medium text-zinc-700 dark:text-zinc-300" for="mnemonic">
			Workspace recovery phrase (12 words)
		</label>
		<input
			id="mnemonic"
			type="text"
			bind:value={mnemonic}
			placeholder="apple banana cherry ..."
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={loggingIn || !mnemonic.trim()}
			class="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			{loggingIn ? 'Signing in…' : 'Enter workspace'}
		</button>
		{#if loginError}
			<p class="text-sm text-red-600 dark:text-red-400">{loginError}</p>
		{/if}
		<p class="text-xs text-zinc-500">
			No workspace yet? Any valid 12-word phrase creates one on first login (US-001).
		</p>
	</form>
</main>
