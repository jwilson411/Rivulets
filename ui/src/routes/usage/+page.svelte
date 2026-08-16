<script lang="ts">
	import { usage, type Usage, type UsageRange } from '$lib/api/usage';
	import { agentInk, INK_SWATCH } from '$lib/ink';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Usage (06-screens.md → Usage, mockup 2l): three large stats, a 48px
	// Day/Week/Month segmented control, and bars by agent and model.

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
		} catch {
			loadError = "Couldn't load usage.";
		} finally {
			loading = false;
		}
	}

	refresh();

	function compactTokens(n: number): string {
		if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
		if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
		return n.toLocaleString();
	}

	function formatCost(cost: number | null): string {
		if (cost === null) return '—';
		return cost < 1 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
	}

	function pct(value: number, total: number): number {
		if (total <= 0) return 0;
		return Math.round((value / total) * 100);
	}

	function barWidth(value: number, total: number): number {
		if (total <= 0) return 0;
		return Math.max((value / total) * 100, 2);
	}
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-7 flex flex-wrap items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Usage</h1>
		<div
			role="group"
			aria-label="Time window"
			class="flex h-12 overflow-hidden rounded-xl border border-line bg-surface dark:border-line-dark dark:bg-surface-dark"
		>
			{#each RANGE_OPTIONS as option (option.value)}
				<button
					type="button"
					aria-pressed={range === option.value}
					onclick={() => {
						range = option.value;
						refresh();
					}}
					class="flex items-center px-5 text-[15px] {range === option.value
						? 'bg-ink font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark'
						: 'font-medium text-muted dark:text-muted-dark'}"
				>
					{option.label}
				</button>
			{/each}
		</div>
	</div>

	{#if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if !data}
		<SkeletonCards count={2} />
	{:else}
		<div class="mb-7 grid grid-cols-1 gap-4 sm:grid-cols-3" class:opacity-60={loading}>
			<div
				class="rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
			>
				<div class="mb-1.5 text-sm text-muted dark:text-muted-dark">Tokens</div>
				<div class="font-display text-[26px] font-semibold text-ink dark:text-ink-dark">
					{compactTokens(data.total_tokens)}
				</div>
			</div>
			<div
				class="rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
			>
				<div class="mb-1.5 text-sm text-muted dark:text-muted-dark">Estimated cost</div>
				<div class="font-display text-[26px] font-semibold text-ink dark:text-ink-dark">
					{formatCost(data.total_cost_usd)}{data.cost_incomplete ? '+' : ''}
				</div>
			</div>
			<div
				class="rounded-2xl border border-line bg-surface px-6 py-5 dark:border-line-dark dark:bg-surface-dark"
			>
				<div class="mb-1.5 text-sm text-muted dark:text-muted-dark">Runs</div>
				<div class="font-display text-[26px] font-semibold text-ink dark:text-ink-dark">
					{data.run_count}
				</div>
			</div>
		</div>

		{#if data.cost_incomplete}
			<p class="mb-6 text-[13px] text-muted dark:text-muted-dark">
				One or more models in this window have no price on file — the cost above is a floor, marked
				with "+".
			</p>
		{/if}

		{#if data.run_count === 0}
			<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
				Nothing has run in this window.
			</p>
		{:else}
			<div class="mb-3 text-sm font-semibold text-ink dark:text-ink-dark">By agent</div>
			<div class="mb-8 flex flex-col gap-2.5">
				{#each data.by_agent as row, i (row.agent_id)}
					<div class="flex items-center gap-3">
						<span class="w-24 flex-none truncate text-[15px] text-ink dark:text-ink-dark">
							{row.agent_name}
						</span>
						<div class="h-3 flex-1 overflow-hidden rounded-full bg-line dark:bg-line-dark">
							<div
								class="h-full rounded-full {INK_SWATCH[agentInk(i)]}"
								style="width: {barWidth(row.total_tokens, data.total_tokens)}%"
							></div>
						</div>
						<span class="w-11 flex-none text-right text-sm text-muted dark:text-muted-dark">
							{pct(row.total_tokens, data.total_tokens)}%
						</span>
					</div>
				{/each}
			</div>

			<div class="mb-3 text-sm font-semibold text-ink dark:text-ink-dark">By model</div>
			<div class="flex flex-col gap-2.5">
				{#each data.by_model as row (row.model + (row.tier ?? ''))}
					<div class="flex items-center gap-3">
						<span
							class="w-40 flex-none truncate font-mono text-[13px] text-ink dark:text-ink-dark"
							title={row.model}
						>
							{row.model}
						</span>
						<div class="h-3 flex-1 overflow-hidden rounded-full bg-line dark:bg-line-dark">
							<div
								class="h-full rounded-full bg-ink dark:bg-ink-dark"
								style="width: {barWidth(row.total_tokens, data.total_tokens)}%"
							></div>
						</div>
						<span class="w-11 flex-none text-right text-sm text-muted dark:text-muted-dark">
							{pct(row.total_tokens, data.total_tokens)}%
						</span>
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>
