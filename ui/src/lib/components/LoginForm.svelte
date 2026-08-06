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
		<h1 class="text-3xl font-semibold tracking-tight text-ink dark:text-ink-dark">
			Rivulets<span class="text-agent-cyan-600 dark:text-agent-cyan-400">.</span>
		</h1>
		<p class="mt-1 text-neutral-600 dark:text-neutral-400">
			A local-first workspace where conversations run like small streams — humans and AI agent
			teams, side by side.
		</p>
	</header>

	<form onsubmit={handleLogin} class="flex flex-col gap-3">
		<label class="text-sm font-medium text-ink dark:text-ink-dark" for="mnemonic">
			Workspace recovery phrase (12 words)
		</label>
		<input
			id="mnemonic"
			type="text"
			bind:value={mnemonic}
			placeholder="apple banana cherry ..."
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<button
			type="submit"
			disabled={loggingIn || !mnemonic.trim()}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{loggingIn ? 'Signing in…' : 'Enter workspace'}
		</button>
		{#if loginError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loginError}</p>
		{/if}
		<p class="text-xs text-neutral-500">
			No workspace yet? Any valid 12-word phrase creates one on first login (US-001).
		</p>
	</form>
</main>
