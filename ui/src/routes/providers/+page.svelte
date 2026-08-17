<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';
	import { providers, type Provider, type ProviderKind } from '$lib/api/providers';
	import { initials } from '$lib/ink';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import OwnerOnly from '$lib/components/OwnerOnly.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Providers (06-screens.md → Providers, mockup 2k, owner only): large
	// provider cards as a picker — never a select of slugs. Ollama asks for
	// a local address instead of a key; "OpenAI-compatible" is the human
	// name for openai_compatible (banned-words list, 09-copy-deck.md).

	const PROVIDER_LABELS: Record<ProviderKind, string> = {
		anthropic: 'Anthropic',
		openai: 'OpenAI',
		google: 'Google',
		xai: 'xAI',
		ollama: 'Ollama',
		openai_compatible: 'OpenAI-compatible',
		deepseek: 'DeepSeek',
		mistral: 'Mistral',
		groq: 'Groq',
		qwen: 'Qwen',
		cohere: 'Cohere'
	};
	// The six the picker leads with (mockup 2k); the rest sit behind "More".
	const PRIMARY_KINDS: ProviderKind[] = [
		'anthropic',
		'openai',
		'google',
		'xai',
		'ollama',
		'openai_compatible'
	];
	const MORE_KINDS: ProviderKind[] = ['deepseek', 'mistral', 'groq', 'qwen', 'cohere'];

	let providerList = $state<Provider[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	// null while unknown (still loading, or the lookup failed) -- the
	// warning below only renders once we positively know it's 'fallback',
	// never as a default guess (#118).
	let credentialBackend = $state<'keychain' | 'fallback' | null>(null);

	let kind = $state<ProviderKind>('anthropic');
	let apiKey = $state('');
	let baseUrl = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let deleteError = $state<string | null>(null);

	let needsAddress = $derived(kind === 'ollama' || kind === 'openai_compatible');

	async function refresh() {
		loadError = null;
		try {
			providerList = await providers.list();
		} catch {
			loadError = "Couldn't load providers.";
		} finally {
			loading = false;
		}
	}

	// #351: every endpoint this page talks to is OwnerGrant-only server-side,
	// so a non-owner session skips the fetches and renders <OwnerOnly> below
	// instead of a wall of 403 load errors.
	if (auth.grant === 'owner') {
		refresh();
		providers
			.credentialStorage()
			.then((res) => (credentialBackend = res.backend))
			.catch(() => {
				// Leave credentialBackend null -- silently defaulting to
				// "keychain" here would hide the exact disclosure this
				// endpoint exists to surface.
			});
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (kind !== 'ollama' && !apiKey.trim()) return;
		if (needsAddress && !baseUrl.trim()) {
			createError =
				kind === 'ollama'
					? 'Ollama needs its local address — usually http://localhost:11434.'
					: 'An OpenAI-compatible provider needs its address.';
			return;
		}
		creating = true;
		try {
			await providers.create({
				provider: kind,
				label: PROVIDER_LABELS[kind],
				api_key: kind === 'ollama' ? apiKey.trim() || 'ollama' : apiKey.trim(),
				base_url: baseUrl.trim() || undefined
			});
			apiKey = '';
			baseUrl = '';
			await refresh();
		} catch {
			createError = 'That key was rejected. Check it and try again.';
		} finally {
			creating = false;
		}
	}

	async function handleDelete(id: string) {
		deleteError = null;
		try {
			await providers.remove(id);
			await refresh();
		} catch {
			deleteError = "Couldn't remove that provider. Try again.";
		}
	}
</script>

{#snippet kindButton(k: ProviderKind)}
	<button
		type="button"
		onclick={() => (kind = k)}
		aria-pressed={kind === k}
		class="flex h-[72px] items-center justify-center rounded-xl bg-surface px-2 text-center dark:bg-surface-dark {kind ===
		k
			? 'border-2 border-accent font-semibold text-ink dark:border-accent-dark dark:text-ink-dark'
			: 'border border-line font-medium text-ink hover:border-accent dark:border-line-dark dark:text-ink-dark dark:hover:border-accent-dark'} text-base"
	>
		{PROVIDER_LABELS[k]}
	</button>
{/snippet}

{#if auth.grant !== 'owner'}
	<OwnerOnly title="Providers" />
{:else}
	<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
		<h1 class="mb-6 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">
			Providers
		</h1>

		{#if credentialBackend === 'fallback'}
			<p
				class="mb-6 rounded-xl border border-warn-line bg-warn-soft px-5 py-4 text-sm leading-normal text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
			>
				No OS keychain was found on this install (common under Docker), so provider keys are
				encrypted with a key derived from your recovery phrase instead. Anyone with the phrase can
				read them — keep it just as secret either way.
			</p>
		{/if}

		{#if loading}
			<SkeletonCards count={1} />
		{:else if loadError}
			<ErrorBanner message={loadError} onRetry={refresh} />
		{:else if providerList.length > 0}
			{#if deleteError}
				<p class="mb-3 text-sm text-danger">{deleteError}</p>
			{/if}
			<div class="mb-8 flex flex-col gap-3">
				{#each providerList as provider (provider.id)}
					<div
						class="flex min-h-16 items-center gap-3.5 rounded-2xl border border-line bg-surface px-6 py-4 dark:border-line-dark dark:bg-surface-dark"
					>
						<span
							class="flex h-10 w-10 flex-none items-center justify-center rounded-[14px] bg-ink text-base font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
						>
							{initials(provider.label)}
						</span>
						<span class="min-w-0">
							<span class="block text-base font-semibold text-ink dark:text-ink-dark">
								{provider.label}
							</span>
							<span class="block truncate text-sm text-muted dark:text-muted-dark">
								{provider.base_url ?? 'Key stays on this machine'}
							</span>
						</span>
						<StatusPill tone="accent" dot class="ml-auto">Connected</StatusPill>
						<button
							type="button"
							onclick={() => handleDelete(provider.id)}
							class="flex-none text-sm font-medium text-muted hover:text-danger dark:text-muted-dark"
						>
							Remove
						</button>
					</div>
				{/each}
			</div>
		{/if}

		<form onsubmit={handleCreate} class="flex flex-col gap-4">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">Add a provider</span>
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
				{#each PRIMARY_KINDS as k (k)}
					{@render kindButton(k)}
				{/each}
			</div>
			<details>
				<summary
					class="cursor-pointer text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
				>
					More providers
				</summary>
				<div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
					{#each MORE_KINDS as k (k)}
						{@render kindButton(k)}
					{/each}
				</div>
			</details>

			{#if kind !== 'ollama'}
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="provider-key">
						API key
					</label>
					<input
						id="provider-key"
						type="password"
						autocomplete="off"
						bind:value={apiKey}
						placeholder={kind === 'anthropic' ? 'sk-ant-…' : 'sk-…'}
						class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
					/>
					<p class="text-[13px] text-muted dark:text-muted-dark">We won't show this key again.</p>
				</div>
			{/if}
			{#if needsAddress}
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="provider-url">
						{kind === 'ollama' ? 'Local address' : 'Address'}
					</label>
					<input
						id="provider-url"
						type="text"
						bind:value={baseUrl}
						placeholder="http://localhost:11434"
						class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
					/>
					{#if kind === 'ollama'}
						<p class="text-[13px] text-muted dark:text-muted-dark">
							New to Ollama? See
							<a
								href="https://ollama.com"
								target="_blank"
								rel="noopener noreferrer"
								class="text-accent underline dark:text-accent-dark">ollama.com</a
							> — once it's running, the address is usually http://localhost:11434.
						</p>
					{/if}
				</div>
			{/if}
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
			<Button type="submit" disabled={creating} class="self-start px-6">
				{creating ? 'Saving…' : 'Save key'}
			</Button>
		</form>
	</div>
{/if}
