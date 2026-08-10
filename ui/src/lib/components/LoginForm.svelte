<script lang="ts">
	// #136: offers to generate a fresh BIP-39 phrase client-side, rather than
	// adding a server endpoint for it -- the phrase is the workspace's only
	// credential (security/keys.py's generate_mnemonic, otherwise only
	// exercised by tests), so it should exist in the browser and nowhere
	// else until the user has seen and acknowledged it. auth.login is what
	// finally sends it over the wire, same as a manually-entered phrase.
	import { generateMnemonic } from 'bip39';
	import { auth } from '$lib/api/auth.svelte';

	let mnemonic = $state('');
	let loginError = $state<string | null>(null);
	let loggingIn = $state(false);

	let generatedWords = $state<string[] | null>(null);
	let acknowledged = $state(false);
	let copied = $state(false);

	async function performLogin(phrase: string): Promise<boolean> {
		loginError = null;
		loggingIn = true;
		try {
			await auth.login(phrase.trim());
			return true;
		} catch (err) {
			loginError = err instanceof Error ? err.message : 'Login failed';
			return false;
		} finally {
			loggingIn = false;
		}
	}

	async function handleLogin(event: SubmitEvent) {
		event.preventDefault();
		if (await performLogin(mnemonic)) mnemonic = '';
	}

	function generatePhrase() {
		// 128 bits of entropy -> 12 words, matching the server's
		// _MNEMONIC_STRENGTH_BITS (security/keys.py).
		generatedWords = generateMnemonic(128).split(' ');
		acknowledged = false;
		copied = false;
		loginError = null;
	}

	function useExistingPhraseInstead() {
		generatedWords = null;
		acknowledged = false;
		copied = false;
		loginError = null;
	}

	async function copyGeneratedPhrase() {
		if (!generatedWords) return;
		await navigator.clipboard.writeText(generatedWords.join(' '));
		copied = true;
	}

	async function confirmGeneratedPhrase() {
		if (!generatedWords || !acknowledged) return;
		if (await performLogin(generatedWords.join(' '))) {
			generatedWords = null;
			acknowledged = false;
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
			A local-first workspace where conversations run like small streams — humans and AI agent
			teams, side by side.
		</p>
	</header>

	{#if generatedWords}
		<div class="flex flex-col gap-4">
			<div
				class="rounded-md border border-agent-magenta-700 bg-agent-magenta-100 p-4 text-sm text-agent-magenta-900 dark:border-agent-magenta-400 dark:bg-agent-magenta-900/40 dark:text-agent-magenta-100"
			>
				<p class="font-semibold">This phrase is the only way into this workspace.</p>
				<p class="mt-1">
					There's no password reset and no server-side recovery. Anyone who has it can open this
					workspace and everything in it — and if you lose it, everything in it is gone for good.
					Write it down or save it in a password manager before you continue.
				</p>
			</div>

			<div
				class="grid grid-cols-3 gap-x-3 gap-y-2 rounded-md border border-ink/15 p-4 dark:border-white/15"
			>
				{#each generatedWords as word, i (i)}
					<div class="flex items-baseline gap-1.5 text-sm">
						<span class="w-4 text-right text-xs text-neutral-500 tabular-nums">{i + 1}.</span>
						<span class="font-mono text-ink dark:text-ink-dark">{word}</span>
					</div>
				{/each}
			</div>

			<button
				type="button"
				onclick={copyGeneratedPhrase}
				class="self-start text-sm font-medium text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
			>
				{copied ? 'Copied' : 'Copy phrase'}
			</button>

			<label class="flex items-start gap-2 text-sm text-ink dark:text-ink-dark">
				<input type="checkbox" bind:checked={acknowledged} class="mt-0.5" />
				I've saved this phrase somewhere safe
			</label>

			<div class="flex items-center gap-4">
				<button
					type="button"
					disabled={loggingIn || !acknowledged}
					onclick={confirmGeneratedPhrase}
					class="rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
				>
					{loggingIn ? 'Signing in…' : 'Enter workspace'}
				</button>
				<button
					type="button"
					onclick={useExistingPhraseInstead}
					class="text-sm text-neutral-600 hover:underline dark:text-neutral-400"
				>
					Use a phrase I already have
				</button>
			</div>

			{#if loginError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loginError}</p>
			{/if}
		</div>
	{:else}
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
			<button
				type="button"
				onclick={generatePhrase}
				class="self-start rounded-md border border-ink/15 px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-agent-cyan-600 dark:border-white/15 dark:text-ink-dark"
			>
				Generate a recovery phrase for me
			</button>
			<p class="text-xs text-neutral-500">
				New here? Generating a phrase sets up your workspace on first login — no account or email
				required, just the phrase.
			</p>
		</form>
	{/if}
</main>
