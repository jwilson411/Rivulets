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
		.map((c) => ({
			id: c.id,
			source: c.from_node_id,
			target: c.to_node_id,
			type: 'smoothstep'
		}));

	return { nodes: flowNodes, edges: flowEdges, entryNodeId };
}
