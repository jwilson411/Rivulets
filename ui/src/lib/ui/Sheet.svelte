<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	// Create/edit sheet (03-design-direction.md): 560px surface, 24px
	// padding, sticky footer with Cancel + primary. Also the pattern for
	// destructive confirms — never window.confirm.
	let {
		title,
		onClose,
		width = 560,
		children,
		footer
	}: {
		title: string;
		onClose: () => void;
		width?: number;
		children: Snippet;
		footer?: Snippet;
	} = $props();

	function onBackdropKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') onClose();
	}
</script>

<svelte:window onkeydown={onBackdropKeydown} />

<div
	class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-4 sm:p-10 dark:bg-black/60"
	onclick={(e) => {
		if (e.target === e.currentTarget) onClose();
	}}
	role="presentation"
>
	<div
		role="dialog"
		aria-modal="true"
		aria-label={title}
		class="flex w-full flex-col rounded-2xl bg-surface shadow-pop dark:bg-surface-dark"
		style="max-width: {width}px"
	>
		<div class="flex items-center justify-between px-6 pt-6">
			<h2 class="font-display text-[22px] font-semibold text-ink dark:text-ink-dark">{title}</h2>
			<button
				type="button"
				onclick={onClose}
				aria-label="Close"
				class="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-paper dark:text-muted-dark dark:hover:bg-paper-dark"
			>
				<Icon name="close" class="h-5 w-5" />
			</button>
		</div>
		<div class="flex flex-col gap-5 px-6 py-5">
			{@render children()}
		</div>
		{#if footer}
			<div
				class="flex items-center justify-end gap-3 border-t border-line px-6 py-5 dark:border-line-dark"
			>
				{@render footer()}
			</div>
		{/if}
	</div>
</div>
