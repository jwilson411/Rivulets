<script lang="ts">
	import type { Snippet } from 'svelte';

	// Large filter chips (Approvals, Workflows, Settings tabs): selected is
	// the ink plate, unselected is a quiet surface chip.
	let {
		selected = false,
		onclick,
		class: className = '',
		children
	}: {
		selected?: boolean;
		onclick?: (event: MouseEvent) => void;
		class?: string;
		children: Snippet;
	} = $props();
</script>

<button
	type="button"
	{onclick}
	aria-pressed={selected}
	class="inline-flex h-10 flex-none items-center rounded-lg px-4 text-[15px] transition-colors {selected
		? 'bg-ink font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark'
		: 'border border-line bg-surface font-medium text-muted hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:text-muted-dark dark:hover:border-accent-dark'} {className}"
>
	{@render children()}
</button>
