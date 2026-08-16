<script lang="ts">
	import type { Snippet } from 'svelte';

	let { class: className = '', children }: { class?: string; children: Snippet } = $props();
</script>

<div
	class="text-[13px] font-semibold tracking-[.04em] text-muted uppercase dark:text-muted-dark {className}"
>
	{@render children()}
</div>
