<script module lang="ts">
	export interface AgentFormValues {
		name: string;
		description: string;
		instructions: string;
		model: string;
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import type { Provider } from '$lib/api/providers';
	import ModelPicker from './ModelPicker.svelte';

	let {
		providers,
		initial = { name: '', description: '', instructions: '', model: '' },
		submitLabel,
		busyLabel,
		busy = false,
		error = null,
		onsubmit,
		oncancel
	}: {
		providers: Provider[];
		initial?: AgentFormValues;
		submitLabel: string;
		busyLabel: string;
		busy?: boolean;
		error?: string | null;
		onsubmit: (values: AgentFormValues) => void;
		oncancel?: () => void;
	} = $props();

	// A snapshot, taken once -- the parent remounts this component (via a
	// keyed #each/#key block) rather than expecting these fields to track
	// `initial` live, so the one-time read here is intentional.
	let name = $state(untrack(() => initial.name));
	let description = $state(untrack(() => initial.description));
	let instructions = $state(untrack(() => initial.instructions));
	let model = $state(untrack(() => initial.model));

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim() || !description.trim() || !instructions.trim() || !model.trim()) return;
		onsubmit({
			name: name.trim(),
			description: description.trim(),
			instructions: instructions.trim(),
			model: model.trim()
		});
	}
</script>

<form onsubmit={handleSubmit} class="flex flex-col gap-3">
	<input
		type="text"
		bind:value={name}
		placeholder="Name"
		class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
	/>
	<input
		type="text"
		bind:value={description}
		placeholder="Description (used by the dispatcher for routing)"
		class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
	/>
	<textarea
		bind:value={instructions}
		placeholder="Instructions (system prompt)"
		rows="3"
		class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
	></textarea>
	<ModelPicker {providers} bind:value={model} />
	<div class="flex items-center gap-3">
		<button
			type="submit"
			disabled={busy}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{busy ? busyLabel : submitLabel}
		</button>
		{#if oncancel}
			<button
				type="button"
				onclick={oncancel}
				class="text-sm text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
			>
				Cancel
			</button>
		{/if}
	</div>
	{#if error}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{error}</p>
	{/if}
</form>
