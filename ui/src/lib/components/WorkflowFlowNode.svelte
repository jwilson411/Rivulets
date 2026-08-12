<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Icon from './Icon.svelte';
	import { NODE_TYPE_ICONS, NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';
	import type { WorkflowFlowNode } from '$lib/workflowFlowGraph';

	let { id, data }: NodeProps<WorkflowFlowNode> = $props();

	// #200: while a merge node is selected, its fan-out ancestor (where its
	// branches diverged) and everything on the paths between them gets a
	// cyan highlight -- ring box-shadows can't be dashed, so the ancestor
	// uses a dashed border to read as the "split point" (also called out
	// with a pill badge below), distinct from the entry node's solid cyan
	// ring; the rest of the branch gets a lighter, plain ring.
	const highlightClass = $derived(
		data.mergeHighlight === 'ancestor'
			? 'border-2 border-dashed border-agent-cyan-600 ring-2 ring-agent-cyan-600/30'
			: data.mergeHighlight === 'branch'
				? 'border-agent-cyan-600/60 ring-1 ring-agent-cyan-600/30'
				: data.isEntry
					? 'border-agent-cyan-600 ring-2 ring-agent-cyan-600/40'
					: 'border-ink/12 dark:border-white/10'
	);

	// #201: drilling into a nested workflow node navigates to its own
	// canvas rather than opening this node's edit panel -- stopPropagation
	// keeps the click from also reaching SvelteFlow's onnodeclick (wired to
	// startEditNode in +page.svelte), which lives on an ancestor of this
	// button.
	function openNestedWorkflow(event: MouseEvent) {
		event.stopPropagation();
		if (data.childWorkflowId) goto(resolve('/workflows/[id]', { id: data.childWorkflowId }));
	}
</script>

<div
	data-testid={`workflow-node-${id}`}
	data-entry={data.isEntry}
	data-merge-highlight={data.mergeHighlight ?? undefined}
	class="w-56 rounded-lg border bg-surface p-3 shadow-sm dark:bg-surface-dark {highlightClass}"
>
	<Handle
		type="target"
		position={Position.Left}
		data-testid={`workflow-node-${id}-target-handle`}
	/>

	<div class="flex items-center justify-between gap-2">
		<span
			class="flex items-center gap-1 rounded-sm bg-neutral-200 px-1.5 py-0.5 text-[11px] font-medium text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
		>
			<Icon
				name={NODE_TYPE_ICONS[data.nodeType]}
				class="h-3 w-3 {data.nodeType === 'human_input'
					? 'text-amber-600 dark:text-amber-400'
					: ''}"
			/>
			{NODE_TYPE_LABELS[data.nodeType]}
		</span>
		{#if data.isEntry}
			<span
				class="rounded-full bg-agent-cyan-100 px-1.5 py-0.5 text-[10px] font-medium text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400"
			>
				Start
			</span>
		{:else if data.mergeHighlight === 'ancestor'}
			<span
				class="rounded-full bg-agent-cyan-100 px-1.5 py-0.5 text-[10px] font-medium text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400"
			>
				Splits here
			</span>
		{/if}
	</div>

	<p class="mt-1 truncate font-medium text-ink dark:text-ink-dark" title={data.label}>
		{data.label}
	</p>
	{#if data.subtitle}
		<p class="mt-0.5 truncate text-xs text-neutral-500" title={data.subtitle}>
			{data.subtitle}
		</p>
	{/if}
	{#if data.childWorkflowId}
		<button
			type="button"
			data-testid={`workflow-node-${id}-open-nested`}
			class="nodrag nopan mt-1.5 flex items-center gap-1 text-xs font-medium text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
			onclick={openNestedWorkflow}
		>
			<Icon name="external-link" class="h-3 w-3" />
			Open workflow
		</button>
	{/if}

	<Handle
		type="source"
		position={Position.Right}
		data-testid={`workflow-node-${id}-source-handle`}
	/>
</div>
