<script module lang="ts">
	// The MIME type carried on the drag -- WorkflowFlowCanvas's dragover
	// handler checks for this exact string so it never treats an unrelated
	// browser drag (e.g. dragging text into the page) as a node drop.
	export const PALETTE_DRAG_MIME = 'application/x-rivulets-node-type';
</script>

<script lang="ts">
	import type { WorkflowNodeType } from '$lib/api/workflows';
	import { NODE_TYPE_ICONS, NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';
	import Icon from './Icon.svelte';

	const nodeTypes = Object.keys(NODE_TYPE_LABELS) as WorkflowNodeType[];

	function handleDragStart(event: DragEvent, nodeType: WorkflowNodeType) {
		if (!event.dataTransfer) return;
		event.dataTransfer.setData(PALETTE_DRAG_MIME, nodeType);
		event.dataTransfer.effectAllowed = 'copy';
	}
</script>

<div class="flex flex-wrap gap-2 border-b border-line p-3 dark:border-line-dark">
	{#each nodeTypes as nodeType (nodeType)}
		<div
			data-testid={`palette-node-${nodeType}`}
			draggable="true"
			ondragstart={(e) => handleDragStart(e, nodeType)}
			class="flex h-9 cursor-grab items-center gap-1.5 rounded-md border border-line bg-surface px-3.5 text-sm font-medium text-ink select-none active:cursor-grabbing dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
		>
			<Icon name={NODE_TYPE_ICONS[nodeType]} class="h-3.5 w-3.5 text-muted dark:text-muted-dark" />
			{NODE_TYPE_LABELS[nodeType]}
		</div>
	{/each}
</div>
