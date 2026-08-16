<script lang="ts">
	import type { Snippet } from 'svelte';

	// Wide Stream controls (03-design-direction.md): primary = accent fill,
	// 48px tall, 16px radius, verb label. Secondary = surface + 1px line.
	// Destructive = surface + 1px danger + danger text (confirm in a sheet,
	// never window.confirm).
	let {
		variant = 'primary',
		size = 'lg',
		type = 'button',
		disabled = false,
		class: className = '',
		title,
		onclick,
		children
	}: {
		variant?: 'primary' | 'secondary' | 'destructive' | 'warn';
		size?: 'lg' | 'md';
		type?: 'button' | 'submit';
		disabled?: boolean;
		class?: string;
		title?: string;
		onclick?: (event: MouseEvent) => void;
		children: Snippet;
	} = $props();

	const variants: Record<string, string> = {
		primary:
			'bg-accent text-white hover:bg-accent-deep dark:bg-accent-dark dark:text-paper-dark dark:hover:opacity-90',
		secondary:
			'bg-surface text-ink border border-line hover:border-accent dark:bg-surface-dark dark:text-ink-dark dark:border-line-dark dark:hover:border-accent-dark',
		destructive:
			'bg-surface text-danger border border-danger hover:bg-danger-soft dark:bg-surface-dark dark:hover:bg-danger-soft-dark',
		warn: 'bg-warn text-white hover:opacity-90'
	};

	const sizes: Record<string, string> = {
		lg: 'h-12 rounded-xl px-5 text-base',
		md: 'h-10 rounded-lg px-4 text-[15px]'
	};
</script>

<button
	{type}
	{disabled}
	{title}
	{onclick}
	class="inline-flex flex-none items-center justify-center gap-2 font-semibold transition-colors disabled:pointer-events-none disabled:opacity-40 {variants[
		variant
	]} {sizes[size]} {className}"
>
	{@render children()}
</button>
