<script lang="ts">
	import type { Snippet } from 'svelte';

	// Shared empty state: one sentence + one 48px button (06-screens.md).
	let {
		message,
		children
	}: {
		message: string;
		children?: Snippet;
	} = $props();
</script>

<div class="py-6 text-center">
	<p class="mb-4 text-base text-muted dark:text-muted-dark">{message}</p>
	{#if children}
		{@render children()}
	{/if}
</div>
