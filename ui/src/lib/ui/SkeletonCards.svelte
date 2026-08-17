<script lang="ts">
	// Loading state: skeleton rows, not "Loading…" italic (06-screens.md).
	let { count = 2, class: className = '' }: { count?: number; class?: string } = $props();
</script>

<div class="flex flex-col gap-3 {className}" aria-hidden="true">
	{#each Array.from({ length: count }, (_, index) => index) as i (i)}
		<div
			class="flex flex-col gap-2.5 rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
		>
			<div class="breath h-3.5 w-[55%] rounded-full bg-line dark:bg-line-dark"></div>
			<div class="breath h-3 w-[80%] rounded-full bg-line/60 dark:bg-line-dark/60"></div>
		</div>
	{/each}
</div>
