<script lang="ts">
	import Icon from '$lib/ui/Icon.svelte';
	import { paletteShortcutLabel } from '$lib/format';

	// Visible stand-in for the ⌘K palette (#416). The real search lives
	// in CommandPalette; this is just the discoverable entry point.
	let { onOpen, class: className = '' }: { onOpen: () => void; class?: string } = $props();
</script>

<button
	type="button"
	onclick={onOpen}
	aria-label="Search / jump"
	class="flex h-11 w-full items-center gap-2.5 rounded-lg border border-line bg-surface px-3.5 text-left focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:focus:border-accent-dark {className}"
>
	<Icon name="search" class="h-[18px] w-[18px] flex-none text-muted dark:text-muted-dark" />
	<span class="min-w-0 flex-1 truncate text-[15px] text-muted dark:text-muted-dark">
		Search / jump
	</span>
	<kbd
		class="rounded-md bg-paper px-2 py-1 font-mono text-xs text-muted dark:bg-paper-dark dark:text-muted-dark"
	>
		{paletteShortcutLabel()}
	</kbd>
</button>
