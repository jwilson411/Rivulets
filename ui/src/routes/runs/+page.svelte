<script lang="ts">
	import { resolve } from '$app/paths';
	import { runs, type RunTrace, type RunSpan } from '$lib/api/runs';
	import { timeAgo } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Runs (06-screens.md → Runs, mockup 2g): one end-to-end timeline per
	// human message, slash command, or scheduled workflow fire. Everything
	// here is a "step" in the UI — never a "span" (09-copy-deck.md).

	let traceList = $state<RunTrace[] | null>(null);
	let listError = $state<string | null>(null);

	let expandedTraceId = $state<string | null>(null);
	let spansByTrace = $state<Record<string, RunSpan[]>>({});
	let detailError = $state<string | null>(null);
	let cancelError = $state<string | null>(null);
	let cancelErrorId = $state<string | null>(null);
	let cancellingId = $state<string | null>(null);

	async function refresh() {
		listError = null;
		try {
			traceList = await runs.list();
		} catch {
			listError = "Couldn't load runs.";
		}
	}

	refresh();

	async function toggleTrace(traceId: string) {
		if (expandedTraceId === traceId) {
			expandedTraceId = null;
			return;
		}
		expandedTraceId = traceId;
		if (spansByTrace[traceId]) return;
		detailError = null;
		try {
			const detail = await runs.get(traceId);
			spansByTrace[traceId] = detail.spans;
		} catch {
			detailError = "Couldn't load this run's steps.";
		}
	}

	function statusTone(status: string): 'accent' | 'danger' | 'warn' | 'neutral' {
		if (status === 'completed' || status === 'success') return 'accent';
		if (status === 'error') return 'danger';
		if (status === 'running' || status === 'pending') return 'warn';
		return 'neutral';
	}

	function statusLabel(status: string): string {
		if (status === 'completed') return 'Completed';
		if (status === 'error') return 'Failed';
		if (status === 'running') return 'Running';
		if (status === 'cancelled') return 'Cancelled';
		return status.charAt(0).toUpperCase() + status.slice(1);
	}

	// A running row with no steps that is older than a minute is the
	// leftover #414 is about -- not a request that just opened. The
	// server reaps these after 5 minutes; the warning is so a two-day
	// leftover never just says "Running".
	function isStuckZeroStep(trace: RunTrace): boolean {
		if (trace.status !== 'running' || trace.span_count > 0) return false;
		return Date.now() - new Date(trace.started_at).getTime() > 60_000;
	}

	async function cancelTrace(traceId: string, event: MouseEvent) {
		event.stopPropagation();
		cancelError = null;
		cancelErrorId = null;
		cancellingId = traceId;
		try {
			const updated = await runs.cancel(traceId);
			if (traceList) {
				traceList = traceList.map((t) => (t.id === traceId ? { ...t, ...updated } : t));
			}
		} catch {
			cancelError = "Couldn't cancel that run.";
			cancelErrorId = traceId;
		} finally {
			cancellingId = null;
		}
	}

	function formatCost(cost: number | null): string {
		if (cost === null) return '';
		return cost < 1 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
	}

	function formatDuration(ms: number | null): string {
		if (ms === null) return '';
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	}

	// RunSpan.parent_span_id gives every step a place in the tree; the API
	// returns them flat, ordered by started_at, so nesting is built here
	// rather than server-side.
	interface StepNode {
		span: RunSpan;
		children: StepNode[];
	}

	function buildTree(spans: RunSpan[]): StepNode[] {
		const nodesById: Record<string, StepNode> = {};
		for (const span of spans) nodesById[span.id] = { span, children: [] };
		const roots: StepNode[] = [];
		for (const span of spans) {
			const node = nodesById[span.id];
			const parent = span.parent_span_id ? nodesById[span.parent_span_id] : undefined;
			if (parent) parent.children.push(node);
			else roots.push(node);
		}
		return roots;
	}

	function stepName(span: RunSpan): string {
		if (span.span_type === 'dispatch_decision') return 'Dispatch';
		if (span.span_type === 'workflow_node_run') return `Step · ${span.name}`;
		return span.name;
	}
</script>

<div class="mx-auto max-w-[820px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<h1 class="mb-6 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Runs</h1>

	{#if listError}
		<ErrorBanner message={listError} onRetry={refresh} />
	{:else if traceList === null}
		<SkeletonCards count={3} />
	{:else if traceList.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			Nothing has run yet. Send a message or fire a workflow.
		</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each traceList as trace (trace.id)}
				<div
					class="rounded-2xl border border-line bg-surface dark:border-line-dark dark:bg-surface-dark"
				>
					<div class="flex min-h-16 w-full flex-wrap items-center gap-3 px-6 py-4">
						<button
							type="button"
							onclick={() => toggleTrace(trace.id)}
							aria-expanded={expandedTraceId === trace.id}
							class="flex min-w-0 flex-1 flex-wrap items-center gap-3 text-left"
						>
							<Icon
								name="chevron-right"
								class="h-4 w-4 flex-none text-muted transition-transform duration-150 dark:text-muted-dark {expandedTraceId ===
								trace.id
									? 'rotate-90'
									: ''}"
							/>
							<span
								class="min-w-0 flex-1 truncate text-base font-semibold text-ink dark:text-ink-dark"
							>
								{trace.label}
							</span>
							<StatusPill tone={statusTone(trace.status)}>{statusLabel(trace.status)}</StatusPill>
							<span class="flex-none text-sm text-muted dark:text-muted-dark">
								{trace.span_count} step{trace.span_count === 1 ? '' : 's'}
								{#if trace.total_cost_usd !== null}
									· {formatCost(trace.total_cost_usd)}
								{/if}
								· {timeAgo(trace.started_at)}
							</span>
						</button>
						{#if trace.status === 'running'}
							<Button
								variant="destructive"
								size="md"
								disabled={cancellingId === trace.id}
								onclick={(event) => cancelTrace(trace.id, event)}
							>
								Cancel
							</Button>
						{/if}
					</div>
					{#if isStuckZeroStep(trace)}
						<p class="px-6 pb-3 text-sm text-warn">
							No steps recorded. This run looks interrupted.
						</p>
					{/if}
					{#if cancelError && cancelErrorId === trace.id}
						<p class="px-6 pb-3 text-sm text-danger">{cancelError}</p>
					{/if}
					{#if expandedTraceId === trace.id}
						<div class="border-t border-line px-6 py-4 dark:border-line-dark">
							{#if trace.rivulet_id && trace.channel_id}
								<a
									href={resolve('/channels/[id]/rivulets/[rivuletId]', {
										id: trace.channel_id,
										rivuletId: trace.rivulet_id
									})}
									class="mb-3 inline-block text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
								>
									Open conversation
								</a>
							{/if}
							{#if detailError}
								<p class="text-sm text-danger">{detailError}</p>
							{:else if !spansByTrace[trace.id]}
								<div class="breath h-3 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
							{:else if spansByTrace[trace.id].length === 0}
								<p class="text-sm text-muted dark:text-muted-dark">
									No steps recorded for this run.
								</p>
							{:else}
								<div class="ml-1.5 border-l-2 border-line pl-4.5 dark:border-line-dark">
									{#each buildTree(spansByTrace[trace.id]) as node (node.span.id)}
										{@render stepRow(node, 0)}
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#snippet stepRow(node: StepNode, depth: number)}
	<div class="flex flex-col gap-1 py-1.5" style="margin-left: {depth * 16}px">
		<div class="flex flex-wrap items-center gap-2.5 text-[15px]">
			<span class="font-semibold text-ink dark:text-ink-dark">{stepName(node.span)}</span>
			{#if node.span.status !== 'completed'}
				<StatusPill tone={statusTone(node.span.status)} class="h-5 text-xs">
					{statusLabel(node.span.status)}
				</StatusPill>
			{/if}
			<span class="ml-auto flex-none text-[13px] text-muted dark:text-muted-dark">
				{formatDuration(node.span.duration_ms)}
				{#if node.span.cost_usd !== null}
					· {formatCost(node.span.cost_usd)}
				{/if}
			</span>
		</div>
		{#if node.span.tool_calls.length > 0}
			<div class="flex flex-col gap-1 pl-4">
				{#each node.span.tool_calls as call (call.id)}
					<div class="flex items-center gap-2 text-[13px] text-muted dark:text-muted-dark">
						<span class="font-mono">{call.tool_name}</span>
						{#if call.sensitive}
							<StatusPill tone="warn" class="h-5 text-xs">Sensitive</StatusPill>
						{/if}
						{#if call.status !== 'success'}
							<StatusPill tone="danger" class="h-5 text-xs">{call.status}</StatusPill>
						{/if}
						<span class="ml-auto flex-none">{formatDuration(call.duration_ms)}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
	{#each node.children as child (child.span.id)}
		{@render stepRow(child, depth + 1)}
	{/each}
{/snippet}
