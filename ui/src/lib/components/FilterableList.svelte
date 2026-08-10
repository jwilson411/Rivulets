<script module lang="ts">
	export interface ListFilter<T> {
		id: string;
		label: string;
		options: { value: string; label: string }[];
		predicate: (item: T, value: string) => boolean;
	}
</script>

<script lang="ts" generics="T">
	// Shared chrome for the six management list screens (agents, teams,
	// providers, mcp-servers, tools, workflows — see #135/#150). Owns
	// search/filter state and pagination; callers own item markup via the
	// `item` snippet so each screen's existing <li> layout doesn't need to
	// change shape to adopt this.
	//
	// Pagination rather than DOM virtualization: item rows here have
	// unpredictable height (expandable routing rules, tool schemas, version
	// history), which defeats the fixed/measured-row-height assumption most
	// virtualization approaches need. Capping rendered rows per page bounds
	// DOM size the same way without that constraint.
	import { untrack, type Snippet } from 'svelte';

	let {
		items,
		getKey,
		searchPlaceholder = 'Search…',
		searchPredicate,
		filters = [],
		pageSize = 25,
		emptyMessage = 'Nothing here yet.',
		noMatchMessage = 'No items match your search.',
		item
	}: {
		items: T[];
		getKey: (item: T) => string;
		searchPlaceholder?: string;
		searchPredicate?: (item: T, query: string) => boolean;
		filters?: ListFilter<T>[];
		pageSize?: number;
		emptyMessage?: string;
		noMatchMessage?: string;
		// Each invocation should render one <li> — FilterableList supplies the <ul>.
		item: Snippet<[T]>;
	} = $props();

	let query = $state('');
	// Filter definitions are static per screen (defined once, not
	// recreated per render), so this only needs the initial snapshot —
	// untrack silences the "did you mean $derived" warning that would
	// otherwise fire for reading a prop outside a reactive context.
	let filterValues = $state<Record<string, string>>(
		untrack(() => Object.fromEntries(filters.map((f) => [f.id, ''])))
	);
	let page = $state(1);

	const filtered = $derived(
		items.filter((it) => {
			if (searchPredicate && query.trim() && !searchPredicate(it, query.trim())) return false;
			return filters.every((f) => {
				const value = filterValues[f.id];
				return !value || f.predicate(it, value);
			});
		})
	);

	// A filter/search change can strand the user on a now-empty page (e.g.
	// they were on page 3, the result set shrank to one page) — snap back
	// to page 1 whenever the filtered set changes.
	$effect(() => {
		void filtered;
		page = 1;
	});

	const totalPages = $derived(Math.max(1, Math.ceil(filtered.length / pageSize)));
	const pageItems = $derived(filtered.slice((page - 1) * pageSize, page * pageSize));
</script>

<div class="flex flex-col gap-3">
	{#if items.length > 0 && (searchPredicate || filters.length > 0)}
		<div class="flex flex-wrap items-center gap-2">
			{#if searchPredicate}
				<input
					type="search"
					bind:value={query}
					placeholder={searchPlaceholder}
					aria-label={searchPlaceholder}
					class="min-w-48 flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			{/if}
			{#each filters as f (f.id)}
				<select
					bind:value={filterValues[f.id]}
					aria-label={f.label}
					class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink dark:border-white/15 dark:text-ink-dark"
				>
					<option value="">{f.label}: All</option>
					{#each f.options as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
			{/each}
		</div>
	{/if}

	{#if items.length === 0}
		<p class="text-sm text-neutral-500 italic">{emptyMessage}</p>
	{:else if filtered.length === 0}
		<p class="text-sm text-neutral-500 italic">{noMatchMessage}</p>
	{:else}
		<ul class="flex flex-col gap-3">
			{#each pageItems as it (getKey(it))}
				{@render item(it)}
			{/each}
		</ul>
		{#if totalPages > 1}
			<div class="flex items-center justify-between text-xs text-neutral-500">
				<button
					type="button"
					onclick={() => (page = Math.max(1, page - 1))}
					disabled={page === 1}
					class="rounded-md border border-ink/15 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/15"
				>
					Previous
				</button>
				<span>Page {page} of {totalPages} ({filtered.length} items)</span>
				<button
					type="button"
					onclick={() => (page = Math.min(totalPages, page + 1))}
					disabled={page === totalPages}
					class="rounded-md border border-ink/15 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/15"
				>
					Next
				</button>
			</div>
		{/if}
	{/if}
</div>
