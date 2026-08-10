<script module lang="ts">
	export interface AgentFormValues {
		name: string;
		description: string;
		instructions: string;
		model: string;
		fallback_models: string[];
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import type { Provider } from '$lib/api/providers';
	import ModelPicker from './ModelPicker.svelte';

	let {
		providers,
		initial = { name: '', description: '', instructions: '', model: '', fallback_models: [] },
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
	// Each entry gets its own reactive box so a keyed #each can bind a
	// ModelPicker to it directly (ModelPicker takes a single bindable
	// string, not an array + index).
	let fallbackModels = $state(
		untrack(() => initial.fallback_models.map((m) => ({ id: crypto.randomUUID(), value: m })))
	);

	function addFallback() {
		fallbackModels.push({ id: crypto.randomUUID(), value: '' });
	}

	function removeFallback(id: string) {
		fallbackModels = fallbackModels.filter((f) => f.id !== id);
	}

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim() || !description.trim() || !instructions.trim() || !model.trim()) return;
		onsubmit({
			name: name.trim(),
			description: description.trim(),
			instructions: instructions.trim(),
			model: model.trim(),
			fallback_models: fallbackModels.map((f) => f.value.trim()).filter((v) => v !== '')
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
	<div class="flex flex-col gap-1">
		<input
			type="text"
			bind:value={description}
			placeholder="Description"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<p class="text-xs text-neutral-500">
			Shown to humans and other agents — a short summary of what this agent does.
		</p>
	</div>
	<div class="flex flex-col gap-1">
		<textarea
			bind:value={instructions}
			placeholder="Instructions"
			rows="6"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		></textarea>
		<p class="text-xs text-neutral-500">
			Tells the agent how to behave — its private instructions, not shown to other agents.
		</p>
	</div>
	<ModelPicker {providers} bind:value={model} />

	<div class="flex flex-col gap-2">
		<div class="flex items-center justify-between">
			<span class="text-xs font-medium text-neutral-600 dark:text-neutral-400">
				Fallback models (tried in order if the model above fails)
			</span>
			<button
				type="button"
				onclick={addFallback}
				class="text-xs font-medium text-agent-cyan-600 hover:underline"
			>
				+ Add fallback
			</button>
		</div>
		{#each fallbackModels as fallback, index (fallback.id)}
			<div class="flex items-start gap-2">
				<span class="mt-2 text-xs text-neutral-500">{index + 1}.</span>
				<div class="flex-1">
					<ModelPicker {providers} bind:value={fallback.value} showAuto={false} />
				</div>
				<button
					type="button"
					onclick={() => removeFallback(fallback.id)}
					class="mt-1 text-xs text-neutral-500 hover:text-agent-magenta-700 dark:hover:text-agent-magenta-400"
					aria-label="Remove fallback model"
				>
					Remove
				</button>
			</div>
		{/each}
	</div>

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
