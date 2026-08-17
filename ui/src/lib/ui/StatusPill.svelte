<script lang="ts">
	import type { Snippet } from 'svelte';

	// 24px status pill. `dot` adds a leading dot; `live` makes it breathe
	// (the design's only status motion, 1.2s — .breath respects
	// prefers-reduced-motion in layout.css).
	let {
		tone = 'accent',
		dot = false,
		live = false,
		class: className = '',
		children
	}: {
		tone?: 'accent' | 'warn' | 'danger' | 'neutral';
		dot?: boolean;
		live?: boolean;
		class?: string;
		children: Snippet;
	} = $props();

	const tones: Record<string, string> = {
		accent: 'bg-accent-soft text-accent dark:bg-accent-soft-dark dark:text-accent-dark',
		warn: 'bg-warn-soft text-warn dark:bg-warn-soft-dark dark:text-warn-ink-dark',
		danger: 'bg-danger-soft text-danger dark:bg-danger-soft-dark dark:text-danger-ink-dark',
		neutral: 'bg-paper text-muted dark:bg-paper-dark dark:text-muted-dark'
	};
</script>

<span
	class="inline-flex h-6 flex-none items-center gap-1.5 rounded-full px-2.5 text-[13px] font-semibold {tones[
		tone
	]} {className}"
>
	{#if dot || live}
		<span class="h-1.5 w-1.5 rounded-full bg-current {live ? 'breath' : ''}"></span>
	{/if}
	{@render children()}
</span>
