<script lang="ts">
	// Shared error state (06-screens.md): quiet banner + a retry button.
	// Copy pattern: "Couldn't load conversations. Try again." — never a raw
	// exception stack.
	let {
		message,
		onRetry,
		class: className = ''
	}: {
		message: string;
		onRetry?: () => void;
		class?: string;
	} = $props();
</script>

<div
	class="flex items-center gap-3.5 rounded-xl border border-danger-line bg-danger-soft px-5 py-4 dark:border-danger-line-dark dark:bg-danger-soft-dark {className}"
>
	<span class="text-[15px] text-danger-ink dark:text-danger-ink-dark">{message}</span>
	{#if onRetry}
		<button
			type="button"
			onclick={onRetry}
			class="ml-auto inline-flex h-12 flex-none items-center rounded-xl border border-line bg-surface px-6 text-base font-semibold text-ink dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
		>
			Try again
		</button>
	{/if}
</div>
