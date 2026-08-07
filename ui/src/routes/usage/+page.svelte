<script lang="ts">
	import {
		usage,
		type Usage,
		type UsageByAgent,
		type UsageByModel,
		type UsageRange
	} from '$lib/api/usage';

	const RANGE_OPTIONS: { value: UsageRange; label: string }[] = [
		{ value: 'day', label: 'Day' },
		{ value: 'week', label: 'Week' },
		{ value: 'month', label: 'Month' }
	];

	let range = $state<UsageRange>('week');
	let data = $state<Usage | null>(null);
	let loadError = $state<string | null>(null);
	let loading = $state(false);

	async function refresh() {
		loading = true;
		loadError = null;
		try {
			data = await usage.get(range);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load usage';
		} finally {
			loading = false;
		}
	}

	refresh();

	function formatTokens(n: number): string {
		return n.toLocaleString();
	}

	function formatCost(cost: number | null): string {
		if (cost === null) return '—';
		return cost < 1 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
	}

	function barWidthPct(value: number, max: number): number {
		if (max <= 0) return 0;
		return Math.max((value / max) * 100, 2);
	}

	function maxTokens<T extends { total_tokens: number }>(rows: T[]): number {
		return rows.reduce((m, r) => Math.max(m, r.total_tokens), 0);
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Usage</h1>
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				Token consumption and estimated cost, aggregated across every agent's runs.
			</p>
		</div>
		<div
			role="group"
			aria-label="Time window"
			class="flex shrink-0 rounded-md border border-ink/15 p-0.5 dark:border-white/15"
		>
			{#each RANGE_OPTIONS as option (option.value)}
				<button
					type="button"
					aria-pressed={range === option.value}
					onclick={() => {
						range = option.value;
						refresh();
					}}
					class="rounded-[3px] px-3 py-1 text-xs {range === option.value
						? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
						: 'text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-white/5'}"
				>
					{option.label}
				</button>
			{/each}
		</div>
	</header>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if !data}
		<p class="text-sm text-neutral-500 italic">Loading…</p>
	{:else}
		<div class="grid grid-cols-3 gap-3" class:opacity-60={loading}>
			<div
				class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">Total tokens</p>
				<p class="mt-1 text-xl font-semibold text-ink dark:text-ink-dark">
					{formatTokens(data.total_tokens)}
				</p>
			</div>
			<div
				class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">Estimated cost</p>
				<p class="mt-1 text-xl font-semibold text-ink dark:text-ink-dark">
					{formatCost(data.total_cost_usd)}{data.cost_incomplete ? '+' : ''}
				</p>
			</div>
			<div
				class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<p class="text-xs text-neutral-600 dark:text-neutral-400">Runs</p>
				<p class="mt-1 text-xl font-semibold text-ink dark:text-ink-dark">{data.run_count}</p>
			</div>
		</div>

		<p class="text-xs text-neutral-500">
			Cost is a best-effort estimate from a static per-model price table.
			{#if data.cost_incomplete}
				One or more models in this window aren't in that table — the total above is a floor, not the
				real spend (marked with "+").
			{/if}
			Dispatcher-side calls (classification, routing-rule generation, LLM fallback) aren't broken out
			separately yet.
		</p>

		{#if data.run_count === 0}
			<p class="text-sm text-neutral-500 italic">No agent runs recorded in this window.</p>
		{:else}
			{@const agentMax = maxTokens(data.by_agent)}
			<section class="flex flex-col gap-3">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">By agent</h2>
				<ul class="flex flex-col gap-2">
					{#each data.by_agent as row (row.agent_id)}
						{@render usageRow(row.agent_name, row, agentMax)}
					{/each}
				</ul>
			</section>

			{@const modelMax = maxTokens(data.by_model)}
			<section class="flex flex-col gap-3">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">By model</h2>
				<ul class="flex flex-col gap-2">
					{#each data.by_model as row (row.model + (row.tier ?? ''))}
						{@render usageRow(row.model, row, modelMax, row.tier)}
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

{#snippet usageRow(
	label: string,
	row: UsageByAgent | UsageByModel,
	max: number,
	tier?: string | null
)}
	<li class="rounded-lg border border-ink/12 p-3 dark:border-white/10">
		<div class="mb-1.5 flex items-baseline justify-between gap-2">
			<p class="truncate text-sm font-medium text-ink dark:text-ink-dark">
				{label}
				{#if tier}
					<span
						class="ml-1.5 rounded-sm bg-neutral-200/60 px-1.5 py-0.5 text-[11px] text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
					>
						{tier}
					</span>
				{/if}
			</p>
			<p class="shrink-0 text-xs text-neutral-600 dark:text-neutral-400">
				{formatTokens(row.total_tokens)} tokens · {formatCost(row.cost_usd)} · {row.run_count} run{row.run_count ===
				1
					? ''
					: 's'}
			</p>
		</div>
		<div class="h-1.5 rounded-sm bg-neutral-200/60 dark:bg-white/10">
			<div
				class="h-1.5 rounded-sm bg-agent-cyan-500"
				style="width: {barWidthPct(row.total_tokens, max)}%"
			></div>
		</div>
	</li>
{/snippet}
