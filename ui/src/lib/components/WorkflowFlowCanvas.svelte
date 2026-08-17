<script lang="ts">
	import {
		SvelteFlow,
		Background,
		Controls,
		MiniMap,
		type NodeTypes,
		type Edge,
		type Viewport,
		type ColorMode,
		type Connection
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import WorkflowFlowNode from './WorkflowFlowNode.svelte';
	import WorkflowNodePalette, { PALETTE_DRAG_MIME } from './WorkflowNodePalette.svelte';
	import type { WorkflowNodeType } from '$lib/api/workflows';
	import type { WorkflowFlowNode as FlowNode } from '$lib/workflowFlowGraph';

	const nodeTypes: NodeTypes = { workflowNode: WorkflowFlowNode };

	let {
		nodes,
		edges,
		colorMode,
		onnodeclick,
		onnodesmoved,
		onpalettedrop,
		onconnect,
		onedgeclick,
		readOnly = false
	}: {
		nodes: FlowNode[];
		edges: Edge[];
		colorMode: ColorMode;
		onnodeclick: (nodeId: string) => void;
		onnodesmoved: (updates: { id: string; positionX: number; positionY: number }[]) => void;
		onpalettedrop: (nodeType: WorkflowNodeType, position: { x: number; y: number }) => void;
		onconnect: (connection: Connection) => void;
		onedgeclick: (edgeId: string) => void;
		// #315: an invite-grant session viewing a published workflow can
		// still look at the graph and click into a step's read-only detail
		// (onnodeclick/onedgeclick stay wired either way), but every
		// gesture that would rewrite it -- dragging a palette entry in,
		// moving a node, drawing a new edge -- is disabled here rather
		// than left to round-trip into the 403 api/workflows.py's
		// _require_owner_if_published now returns for the same guest.
		readOnly?: boolean;
	} = $props();

	let viewport = $state<Viewport>({ x: 0, y: 0, zoom: 1 });
	let paneEl: HTMLDivElement | undefined = $state();

	// A palette drag is plain HTML5 DnD, not a Svelte Flow gesture, so
	// there's no store/useSvelteFlow() context to convert screen -> flow
	// coordinates for us (that hook only works inside <SvelteFlow>'s own
	// children). Doing the same math by hand from the bound viewport
	// avoids needing an extra child component just to reach the hook.
	function toFlowPosition(clientX: number, clientY: number): { x: number; y: number } {
		const rect = paneEl?.getBoundingClientRect();
		if (!rect) return { x: 0, y: 0 };
		return {
			x: (clientX - rect.left - viewport.x) / viewport.zoom,
			y: (clientY - rect.top - viewport.y) / viewport.zoom
		};
	}

	function handleDragOver(event: DragEvent) {
		if (readOnly) return;
		if (!event.dataTransfer?.types.includes(PALETTE_DRAG_MIME)) return;
		event.preventDefault();
		event.dataTransfer.dropEffect = 'copy';
	}

	function handleDrop(event: DragEvent) {
		if (readOnly) return;
		const nodeType = event.dataTransfer?.getData(PALETTE_DRAG_MIME);
		if (!nodeType) return;
		event.preventDefault();
		onpalettedrop(nodeType as WorkflowNodeType, toFlowPosition(event.clientX, event.clientY));
	}
</script>

<div class="canvas-root">
	{#if !readOnly}
		<WorkflowNodePalette />
	{/if}
	<div
		bind:this={paneEl}
		data-testid="workflow-canvas"
		class="canvas-pane"
		ondragover={handleDragOver}
		ondrop={handleDrop}
	>
		<div class="canvas-fill">
			<SvelteFlow
				{nodes}
				{edges}
				{nodeTypes}
				fitView
				nodesDraggable={!readOnly}
				nodesConnectable={!readOnly}
				{colorMode}
				bind:viewport
				onnodeclick={(e) => onnodeclick(e.node.id)}
				onnodedragstop={(e) =>
					onnodesmoved(
						e.nodes.map((n) => ({ id: n.id, positionX: n.position.x, positionY: n.position.y }))
					)}
				{onconnect}
				onedgeclick={(e) => onedgeclick(e.edge.id)}
			>
				<Background />
				<Controls showLock={false} />
				<MiniMap />
			</SvelteFlow>
		</div>
	</div>
</div>

<style>
	/* Plain CSS, not Tailwind utilities -- this sizing is structural (it's
	   what gives <SvelteFlow>'s own 100%-height CSS something real to
	   resolve against), and component tests never load the app's Tailwind
	   stylesheet (only +layout.svelte pulls it in). Mirrors why the section
	   wrapper in +page.svelte sets its height via an inline style rather
	   than a class. */
	.canvas-root {
		display: flex;
		flex-direction: column;
		height: 100%;
		/* A hard floor so the board still has room when the parent chain
		   can't provide a height (component tests never load Tailwind;
		   percentage heights resolve to auto without it). */
		min-height: 480px;
	}
	.canvas-pane {
		position: relative;
		min-height: 0;
		flex: 1 1 auto;
	}
	/* Absolute fill so SvelteFlow's own 100% sizing resolves against a
	   definite box even when the pane's height comes from flex. */
	.canvas-fill {
		position: absolute;
		inset: 0;
	}
</style>
