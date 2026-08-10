<script lang="ts">
	// Test-only host: vitest-browser-svelte's render() takes a component plus
	// plain props, but FilterableList's `item` prop is a snippet, which can
	// only be constructed inside a .svelte file's markup (`{#snippet}`). This
	// wraps FilterableList with a minimal renderable item so its tests can
	// exercise search/filter/pagination without needing every consumer
	// screen's real item markup.
	import FilterableList, { type ListFilter } from './FilterableList.svelte';

	interface Widget {
		id: string;
		name: string;
		kind: string;
	}

	let {
		items,
		filters = [],
		pageSize = 25,
		searchPlaceholder = 'Search…'
	}: {
		items: Widget[];
		filters?: ListFilter<Widget>[];
		pageSize?: number;
		searchPlaceholder?: string;
	} = $props();
</script>

<FilterableList
	{items}
	getKey={(w) => w.id}
	{searchPlaceholder}
	searchPredicate={(w, q) => w.name.toLowerCase().includes(q.toLowerCase())}
	{filters}
	{pageSize}
>
	{#snippet item(w: Widget)}
		<li data-testid="widget-{w.id}">{w.name} ({w.kind})</li>
	{/snippet}
</FilterableList>
