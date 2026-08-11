<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import Icon from './Icon.svelte';
	import { NODE_TYPE_ICONS, NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';
	import type { WorkflowFlowNode } from '$lib/workflowFlowGraph';

	let { id, data }: NodeProps<WorkflowFlowNode> = $props();
</script>

<div
	data-testid={`workflow-node-${id}`}
	data-entry={data.isEntry}
	class="w-56 rounded-lg border bg-surface p-3 shadow-sm dark:bg-surface-dark {data.isEntry
		? 'border-agent-cyan-600 ring-2 ring-agent-cyan-600/40'
		: 'border-ink/12 dark:border-white/10'}"
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

	<Handle
		type="source"
		position={Position.Right}
		data-testid={`workflow-node-${id}-source-handle`}
	/>
</div>
