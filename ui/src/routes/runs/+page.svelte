<script lang="ts">
	import { resolve } from '$app/paths';
	import { runs, type RunTrace, type RunSpan } from '$lib/api/runs';
	import { timeAgo } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';

	let traceList = $state<RunTrace[] | null>(null);
	let listError = $state<string | null>(null);

	let expandedTraceId = $state<string | null>(null);
	let spansByTrace = $state<Record<string, RunSpan[]>>({});
	let detailError = $state<string | null>(null);

	async function refresh() {
		listError = null;
		try {
			traceList = await runs.list();
		} catch (err) {
			listError = err instanceof Error ? err.message : 'Failed to load runs';
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
		} catch (err) {
			detailError = err instanceof Error ? err.message : 'Failed to load run detail';
		}
	}

	function statusClass(status: string): string {
		if (status === 'completed')
			return 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400';
		if (status === 'error')
			return 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400';
		return 'bg-neutral-200 text-neutral-700 dark:bg-white/10 dark:text-neutral-300';
	}

	function formatCost(cost: number | null): string {
		if (cost === null) return '—';
		return cost < 1 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
	}

	function formatDuration(ms: number | null): string {
		if (ms === null) return '—';
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	}

	// RunSpan.parent_span_id gives every span a place in the tree; the API
	// returns them flat, ordered by started_at, so nesting is built here
	// rather than server-side -- the same "detail response is flat, tree is
	// a client-side view concern" split the workflow run-history page
	// doesn't need (WorkflowNodeRun has no such nesting), but a trace does.
	interface SpanNode {
		span: RunSpan;
		children: SpanNode[];
	}

	function buildTree(spans: RunSpan[]): SpanNode[] {
		const nodesById: Record<string, SpanNode> = {};
		for (const span of spans) nodesById[span.id] = { span, children: [] };
		const roots: SpanNode[] = [];
		for (const span of spans) {
			const node = nodesById[span.id];
			const parent = span.parent_span_id ? nodesById[span.parent_span_id] : undefined;
			if (parent) parent.children.push(node);
			else roots.push(node);
		}
		return roots;
	}

	function spanTypeLabel(spanType: string): string {
		if (spanType === 'dispatch_decision') return 'dispatch';
		if (spanType === 'agent_run') return 'agent';
		if (spanType === 'workflow_run') return 'workflow';
		if (spanType === 'workflow_node_run') return 'step';
		return spanType;
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Runs</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			One end-to-end timeline per human message, slash command, or scheduled workflow fire —
			dispatch decisions, agent replies, and workflow steps, linked and timed.
		</p>
	</header>

	{#if listError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{listError}</p>
	{:else if traceList === null}
		<p class="text-sm text-neutral-500 italic">Loading…</p>
	{:else if traceList.length === 0}
		<p class="text-sm text-neutral-500 italic">
			No runs recorded yet — this fills in as messages are dispatched or workflows fire.
		</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each traceList as trace (trace.id)}
				<li class="rounded-lg border border-ink/12 dark:border-white/10">
					<button
						type="button"
						onclick={() => toggleTrace(trace.id)}
						class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
					>
						<span class="flex min-w-0 items-center gap-2 text-sm text-ink dark:text-ink-dark">
							<Icon
								name="chevron"
								class="h-3 w-3 flex-none text-neutral-500 transition-transform duration-150 {expandedTraceId ===
								trace.id
									? 'rotate-90'
									: ''}"
							/>
							<span class="truncate">{trace.label}</span>
						</span>
						<span class="flex flex-none items-center gap-3 text-xs text-neutral-500">
							<span>{trace.span_count} span{trace.span_count === 1 ? '' : 's'}</span>
							<span>{formatCost(trace.total_cost_usd)}</span>
							<span>{timeAgo(trace.started_at)}</span>
							<span class="rounded-sm px-2 py-0.5 {statusClass(trace.status)}">
								{trace.status}
							</span>
						</span>
					</button>
					{#if expandedTraceId === trace.id}
						<div class="border-t border-ink/10 px-3 py-2 dark:border-white/10">
							{#if trace.rivulet_id && trace.channel_id}
								<a
									href={resolve('/channels/[id]/rivulets/[rivuletId]', {
										id: trace.channel_id,
										rivuletId: trace.rivulet_id
									})}
									class="mb-2 inline-block text-xs text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
								>
									View rivulet
								</a>
							{/if}
							{#if detailError}
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									{detailError}
								</p>
							{:else if !spansByTrace[trace.id]}
								<p class="text-xs text-neutral-500">Loading spans…</p>
							{:else if spansByTrace[trace.id].length === 0}
								<p class="text-xs text-neutral-500">No spans recorded for this run.</p>
							{:else}
								<ul class="flex flex-col gap-1.5">
									{#each buildTree(spansByTrace[trace.id]) as node (node.span.id)}
										{@render spanRow(node, 0)}
									{/each}
								</ul>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

{#snippet spanRow(node: SpanNode, depth: number)}
	<li class="flex flex-col gap-1 text-xs" style="margin-left: {depth * 16}px">
		<div class="flex items-center gap-2">
			<span
				class="rounded-sm bg-neutral-200/60 px-1.5 py-0.5 text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
			>
				{spanTypeLabel(node.span.span_type)}
			</span>
			<span class="font-medium text-ink dark:text-ink-dark">{node.span.name}</span>
			<span class="rounded-sm px-1.5 py-0.5 {statusClass(node.span.status)}">
				{node.span.status}
			</span>
			<span class="ml-auto flex-none text-neutral-500">
				{formatDuration(node.span.duration_ms)}
				{#if node.span.cost_usd !== null}
					· {formatCost(node.span.cost_usd)}
				{/if}
			</span>
		</div>
		{#if node.span.tool_calls.length > 0}
			<ul class="flex flex-col gap-1 pl-5">
				{#each node.span.tool_calls as call (call.id)}
					<li class="flex items-center gap-2 text-neutral-500">
						<Icon name="wrench" class="h-3 w-3 flex-none" />
						<span class="font-mono text-[11px] text-neutral-600 dark:text-neutral-400"
							>{call.tool_name}</span
						>
						{#if call.sensitive}
							<span
								class="rounded-sm bg-agent-yellow-500/15 px-1 py-0.5 text-[10px] text-agent-yellow-700 dark:text-agent-yellow-500"
							>
								sensitive
							</span>
						{/if}
						<span
							class="rounded-sm px-1.5 py-0.5 {statusClass(
								call.status === 'success' ? 'completed' : 'error'
							)}"
						>
							{call.status}
						</span>
						<span class="ml-auto flex-none">{formatDuration(call.duration_ms)}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</li>
	{#each node.children as child (child.span.id)}
		{@render spanRow(child, depth + 1)}
	{/each}
{/snippet}
