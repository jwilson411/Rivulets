<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { providers, type Provider, type ProviderKind } from '$lib/api/providers';

	const PROVIDER_KINDS: ProviderKind[] = [
		'anthropic',
		'openai',
		'deepseek',
		'google',
		'mistral',
		'groq',
		'xai',
		'qwen',
		'cohere',
		'ollama',
		'openai_compatible'
	];

	let providerList = $state<Provider[]>([]);
	let loadError = $state<string | null>(null);

	let kind = $state<ProviderKind>('anthropic');
	let label = $state('');
	let apiKey = $state('');
	let baseUrl = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let deleteError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			providerList = await providers.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load providers';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (!label.trim()) return;
		if (kind !== 'ollama' && !apiKey.trim()) return;
		if (kind === 'openai_compatible' && !baseUrl.trim()) {
			createError = 'openai_compatible requires a base URL';
			return;
		}
		if (kind === 'ollama' && !baseUrl.trim()) {
			createError = 'ollama requires a base URL (its local host address)';
			return;
		}
		creating = true;
		try {
			await providers.create({
				provider: kind,
				label: label.trim(),
				api_key: apiKey.trim(),
				base_url: baseUrl.trim() || undefined
			});
			label = '';
			apiKey = '';
			baseUrl = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to add provider';
		} finally {
			creating = false;
		}
	}

	async function handleDelete(id: string) {
		deleteError = null;
		try {
			await providers.remove(id);
			await refresh();
		} catch (err) {
			deleteError = err instanceof ApiError ? err.message : 'Failed to remove provider';
		}
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Providers</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			LLM provider keys are stored in your OS keychain (NFR-3.3) — never synced, never shown again
			once saved.
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Add provider</h2>
		<select
			bind:value={kind}
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink dark:border-white/15 dark:text-ink-dark"
		>
			{#each PROVIDER_KINDS as k (k)}
				<option value={k}>{k}</option>
			{/each}
		</select>
		<input
			type="text"
			bind:value={label}
			placeholder="Label (e.g. Anthropic)"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<input
			type="password"
			bind:value={apiKey}
			placeholder={kind === 'ollama' ? 'API key (optional)' : 'API key'}
			autocomplete="off"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<input
			type="text"
			bind:value={baseUrl}
			placeholder={kind === 'openai_compatible' || kind === 'ollama'
				? 'Base URL (required)'
				: 'Base URL (optional override)'}
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<button
			type="submit"
			disabled={creating}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{creating ? 'Adding…' : 'Add provider'}
		</button>
		{#if createError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if providerList.length === 0}
		<p class="text-sm text-neutral-500 italic">No providers configured yet — add one above.</p>
	{:else}
		{#if deleteError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{deleteError}</p>
		{/if}
		<ul class="flex flex-col gap-2">
			{#each providerList as provider (provider.id)}
				<li
					class="flex items-center justify-between rounded-lg border border-ink/12 px-4 py-3 dark:border-white/10"
				>
					<div>
						<p class="font-medium text-ink dark:text-ink-dark">
							{provider.label}
							<span
								class="ml-2 rounded-sm bg-neutral-200 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
							>
								{provider.provider}
							</span>
						</p>
						{#if provider.base_url}
							<p class="text-xs text-neutral-500">{provider.base_url}</p>
						{/if}
					</div>
					<button
						onclick={() => handleDelete(provider.id)}
						class="text-xs text-neutral-500 hover:text-agent-magenta-600"
					>
						Remove
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
