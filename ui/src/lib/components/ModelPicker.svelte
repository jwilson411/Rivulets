<script lang="ts">
	import { untrack } from 'svelte';
	import type { Provider, ProviderKind } from '$lib/api/providers';
	import {
		AUTO_MODEL,
		AUTO_MODEL_VALUE,
		CUSTOM_MODEL_VALUE,
		MODEL_CATALOG
	} from '$lib/modelCatalog';

	let { providers, value = $bindable('') }: { providers: Provider[]; value: string } = $props();

	function parse(raw: string): { provider: string; modelName: string } {
		const idx = raw.indexOf(':');
		if (idx === -1) return { provider: '', modelName: '' };
		return { provider: raw.slice(0, idx), modelName: raw.slice(idx + 1) };
	}

	// Seeded once from the incoming `value` (e.g. an agent being edited) --
	// afterwards this component's own state is the source of truth, and the
	// effect below is what keeps the bindable `value` in sync with it, not
	// the other way round. A caller that needs to show a different model
	// re-mounts this component (both call sites do, via #each/#if keying).
	const initial = untrack(() => parse(value));
	const initialCatalog = initial.provider
		? (MODEL_CATALOG[initial.provider as ProviderKind] ?? [])
		: [];
	const initialKnown = initialCatalog.some((m) => m.id === initial.modelName);

	let selectedIsAuto = $state(untrack(() => value === AUTO_MODEL));
	let selectedProvider = $state(initial.provider);
	let selectedIsCustom = $state(initial.provider !== '' && !initialKnown);
	let selectedCatalogId = $state(initialKnown ? initial.modelName : '');
	let customText = $state(initial.provider !== '' && !initialKnown ? initial.modelName : '');

	$effect(() => {
		if (selectedIsAuto) {
			value = AUTO_MODEL;
		} else if (!selectedProvider) {
			value = '';
		} else if (selectedIsCustom) {
			value = customText.trim() ? `${selectedProvider}:${customText.trim()}` : '';
		} else {
			value = selectedCatalogId ? `${selectedProvider}:${selectedCatalogId}` : '';
		}
	});

	function handleSelectChange(event: Event) {
		const raw = (event.target as HTMLSelectElement).value;
		if (raw === AUTO_MODEL_VALUE) {
			selectedIsAuto = true;
			selectedProvider = '';
			selectedIsCustom = false;
			selectedCatalogId = '';
			customText = '';
			return;
		}
		selectedIsAuto = false;
		const { provider, modelName } = parse(raw);
		selectedProvider = provider;
		if (modelName === CUSTOM_MODEL_VALUE) {
			selectedIsCustom = true;
			selectedCatalogId = '';
		} else {
			selectedIsCustom = false;
			selectedCatalogId = modelName;
			customText = '';
		}
	}

	const selectValue = $derived(
		selectedIsAuto
			? AUTO_MODEL_VALUE
			: selectedProvider
				? `${selectedProvider}:${selectedIsCustom ? CUSTOM_MODEL_VALUE : selectedCatalogId}`
				: ''
	);
</script>

<div class="flex flex-col gap-2">
	<select
		value={selectValue}
		onchange={handleSelectChange}
		class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
	>
		<option value="" disabled selected={selectValue === ''}>Select a model…</option>
		<option value={AUTO_MODEL_VALUE}>Auto — picks cheap or capable per message</option>
		{#each providers as provider (provider.id)}
			<optgroup label="{provider.label} ({provider.provider})">
				{#each MODEL_CATALOG[provider.provider] as model (model.id)}
					<option value="{provider.provider}:{model.id}">{model.label}</option>
				{/each}
				<option value="{provider.provider}:{CUSTOM_MODEL_VALUE}">Custom…</option>
			</optgroup>
		{/each}
	</select>
	{#if selectedIsCustom}
		<input
			type="text"
			bind:value={customText}
			placeholder="model_name (e.g. claude-3-5-haiku-latest)"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
	{/if}
	{#if providers.length === 0}
		<p class="text-xs text-neutral-500">
			No providers configured yet — add one under Providers first.
		</p>
	{/if}
</div>
