<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { providers, type Provider, type ProviderKind } from '$lib/api/providers';

	const PROVIDER_KINDS: ProviderKind[] = ['anthropic', 'openai', 'deepseek', 'openai_compatible'];

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
		if (!label.trim() || !apiKey.trim()) return;
		if (kind === 'openai_compatible' && !baseUrl.trim()) {
			createError = 'openai_compatible requires a base URL';
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
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Providers</h1>
		<p class="text-sm text-zinc-500">
			LLM provider keys are stored in your OS keychain (NFR-3.3) — never synced, never shown again
			once saved.
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-md border border-zinc-200 p-4 dark:border-zinc-800"
	>
		<h2 class="text-sm font-medium text-zinc-700 dark:text-zinc-300">Add provider</h2>
		<select
			bind:value={kind}
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		>
			{#each PROVIDER_KINDS as k (k)}
				<option value={k}>{k}</option>
			{/each}
		</select>
		<input
			type="text"
			bind:value={label}
			placeholder="Label (e.g. Anthropic)"
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<input
			type="password"
			bind:value={apiKey}
			placeholder="API key"
			autocomplete="off"
			class="rounded-md border border-zinc-300 px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<input
			type="text"
			bind:value={baseUrl}
			placeholder={kind === 'openai_compatible'
				? 'Base URL (required)'
				: 'Base URL (optional override)'}
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={creating}
			class="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			{creating ? 'Adding…' : 'Add provider'}
		</button>
		{#if createError}
			<p class="text-sm text-red-600 dark:text-red-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if providerList.length === 0}
		<p class="text-sm text-zinc-400">No providers configured yet — add one above.</p>
	{:else}
		{#if deleteError}
			<p class="text-sm text-red-600 dark:text-red-400">{deleteError}</p>
		{/if}
		<ul class="flex flex-col gap-2">
			{#each providerList as provider (provider.id)}
				<li
					class="flex items-center justify-between rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800"
				>
					<div>
						<p class="font-medium text-zinc-900 dark:text-zinc-50">
							{provider.label}
							<span
								class="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800"
							>
								{provider.provider}
							</span>
						</p>
						{#if provider.base_url}
							<p class="text-xs text-zinc-400">{provider.base_url}</p>
						{/if}
					</div>
					<button
						onclick={() => handleDelete(provider.id)}
						class="text-xs text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
					>
						Remove
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
