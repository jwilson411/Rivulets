import type { Edge, Node } from '@xyflow/svelte';
import type { WorkflowConnection, WorkflowNode, WorkflowNodeType } from './api/workflows';

export interface WorkflowFlowNodeData {
	[key: string]: unknown;
	label: string;
	nodeType: WorkflowNodeType;
	subtitle: string | null;
	isEntry: boolean;
}

export type WorkflowFlowNode = Node<WorkflowFlowNodeData, 'workflowNode'>;

export interface BuildFlowGraphResult {
	nodes: WorkflowFlowNode[];
	edges: Edge[];
	entryNodeId: string | null;
}

// #198: a conditional edge's label -- what a viewer sees on the canvas
// without opening the inspector. Mirrors _validate_condition's shape
// (api/workflows.py): exactly one of contains/not_contains, string value.
// Anything else (null, or a shape validation would've rejected) renders as
// an unconditional edge -- no label, no dashed styling.
export function conditionEdgeLabel(condition: Record<string, unknown> | null): string | null {
	if (!condition || Object.keys(condition).length !== 1) return null;
	if (typeof condition.contains === 'string') return `contains "${condition.contains}"`;
	if (typeof condition.not_contains === 'string') return `not contains "${condition.not_contains}"`;
	return null;
}

// Pure WorkflowNode[]/WorkflowConnection[] -> Svelte Flow {nodes, edges}
// transform. Framework-agnostic on purpose so branch/merge correctness is
// unit-testable without any DOM/browser involvement. Replaces the old
// buildChain(), which walked one outbound connection per node via a Map and
// silently dropped every branch past the first.
export function buildFlowGraph(
	nodes: WorkflowNode[],
	connections: WorkflowConnection[],
	subtitleFor: (node: WorkflowNode) => string | null
): BuildFlowGraphResult {
	const nodeIds = new Set(nodes.map((n) => n.id));
	const entryNodeId = connections.find((c) => c.from_node_id === null)?.to_node_id ?? null;

	const flowNodes: WorkflowFlowNode[] = nodes.map((node) => ({
		id: node.id,
		type: 'workflowNode',
		position: { x: node.position_x ?? 0, y: node.position_y ?? 0 },
		data: {
			label: node.name,
			nodeType: node.node_type,
			subtitle: subtitleFor(node),
			isEntry: node.id === entryNodeId
		}
	}));

	// The entry connection (from_node_id === null) has no source node to draw
	// an edge from -- it's never represented as an Edge at all. The entry
	// node is marked via data.isEntry instead (see WorkflowFlowNode.svelte).
	// Connections referencing a node id no longer present are dropped rather
	// than thrown on, so one stale row only loses its own edge instead of
	// hiding the whole render (an improvement over buildChain(), which
	// aborted the entire walk on a dangling reference).
	//
	// Deliberately no Map/Set keyed by from_node_id or to_node_id here --
	// that's the exact bug class buildChain() had. A node with two outbound
	// connections must produce two edges; two nodes sharing a target must
	// produce two edges.
	const flowEdges: Edge[] = connections
		.filter(
			(c): c is WorkflowConnection & { from_node_id: string } =>
				c.from_node_id !== null && nodeIds.has(c.from_node_id) && nodeIds.has(c.to_node_id)
		)
		.map((c) => {
			const conditionLabel = conditionEdgeLabel(c.condition_json);
			return {
				id: c.id,
				source: c.from_node_id,
				target: c.to_node_id,
				type: 'smoothstep',
				// Lets tests target a specific edge (`page.getByTestId(...)`) the
				// same way workflow-node-${id} does for nodes -- domAttributes is
				// spread straight onto the rendered <g class="svelte-flow__edge">.
				domAttributes: { 'data-testid': `workflow-edge-${c.id}` },
				// A conditional edge gets a label plus a dashed stroke so
				// branching is visible on the canvas itself, not just in the
				// inspector -- an unconditional edge keeps the plain solid line
				// it always had (no extra keys, so existing snapshots/tests for
				// those are untouched).
				...(conditionLabel
					? {
							label: conditionLabel,
							labelStyle: 'font-size: 11px; font-weight: 600; fill: var(--color-agent-magenta);',
							style: 'stroke-dasharray: 5 4; stroke: var(--color-agent-magenta);'
						}
					: {})
			};
		});

	return { nodes: flowNodes, edges: flowEdges, entryNodeId };
}
