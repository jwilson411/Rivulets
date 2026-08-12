import type { Edge, Node } from '@xyflow/svelte';
import type { WorkflowConnection, WorkflowNode, WorkflowNodeType } from './api/workflows';

export interface WorkflowFlowNodeData {
	[key: string]: unknown;
	label: string;
	nodeType: WorkflowNodeType;
	subtitle: string | null;
	isEntry: boolean;
	// #200: set while this node is part of the currently-selected merge
	// node's fan-out (see findMergeFanOut) -- 'ancestor' for the node the
	// branches diverged at, 'branch' for every other node/the merge itself
	// on a path between them, null otherwise.
	mergeHighlight: 'ancestor' | 'branch' | null;
}

export type WorkflowFlowNode = Node<WorkflowFlowNodeData, 'workflowNode'>;

// #200: the nearest ancestor upstream of a merge node from which 2+ of its
// outbound paths reconverge at that merge, plus everything on the paths
// between them. See findMergeFanOut.
export interface MergeFanOut {
	ancestorId: string;
	branchCount: number;
	nodeIds: Set<string>;
	edgeIds: Set<string>;
}

export interface BuildFlowGraphResult {
	nodes: WorkflowFlowNode[];
	edges: Edge[];
	entryNodeId: string | null;
	hasLoop: boolean;
	mergeFanOut: MergeFanOut | null;
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

// #199: mirrors engine.py's MAX_NODE_VISITS_PER_RUN / MAX_TOTAL_STEPS_PER_RUN
// (server/src/rivulets/workflows/engine.py) -- neither value is exposed via
// any API response, so the UI's "here's the safety net" copy just duplicates
// the same two numbers. Keep these in sync if engine.py's constants change.
export const LOOP_MAX_NODE_VISITS = 25;
export const LOOP_MAX_TOTAL_STEPS = 200;

// #199: an edge is a loop edge if its target can already reach its source
// through the rest of the graph -- i.e. drawing this edge closes a cycle,
// the same "revisits an already-visited node" shape engine.py's visit-count
// guard exists for. Plain per-edge BFS rather than a full SCC pass: workflow
// canvases are small and this only runs when the graph changes, not per
// frame.
export function isLoopEdge(
	connections: WorkflowConnection[],
	edge: { from_node_id: string | null; to_node_id: string }
): boolean {
	if (!edge.from_node_id) return false;
	const fromNodeId = edge.from_node_id;
	const adjacency = new Map<string, string[]>();
	for (const c of connections) {
		if (!c.from_node_id) continue;
		const outbound = adjacency.get(c.from_node_id) ?? [];
		outbound.push(c.to_node_id);
		adjacency.set(c.from_node_id, outbound);
	}
	const visited = new Set<string>([edge.to_node_id]);
	const queue = [edge.to_node_id];
	while (queue.length > 0) {
		const current = queue.shift()!;
		if (current === fromNodeId) return true;
		for (const next of adjacency.get(current) ?? []) {
			if (!visited.has(next)) {
				visited.add(next);
				queue.push(next);
			}
		}
	}
	return false;
}

// #199: an edge's label/style, given its condition (see conditionEdgeLabel)
// and whether it's a loop edge (see isLoopEdge). A loop edge keeps whatever
// color a plain/conditional edge would already have (unstyled default, or
// magenta for a condition) and layers a tighter dotted dash + thicker stroke
// on top of it, plus a "↻" marker in the label -- deliberately not a new
// hue, since --color-agent-yellow is reserved for the third agent's print
// ink only (see layout.css) and isn't available for canvas chrome.
//
// #200: `highlighted` layers on top of either of the above -- it's set for
// every edge on the path between a merge node's fan-out ancestor and the
// merge itself, while that merge node is selected (see findMergeFanOut).
// Reuses --color-agent-cyan, the same hue the entry-node ring already uses
// to mean "structurally significant, look here", overriding any condition
// color since the highlight is a transient "look at this" state rather
// than a permanent property of the edge.
function edgeVisual(
	conditionLabel: string | null,
	loop: boolean,
	highlighted: boolean
): { label?: string; labelStyle?: string; style: string } | null {
	if (!conditionLabel && !loop && !highlighted) return null;
	const label = loop ? `↻ ${conditionLabel ?? 'loop back'}` : (conditionLabel ?? undefined);
	const color = conditionLabel ? 'stroke: var(--color-agent-magenta); ' : '';
	let style = loop
		? `${color}stroke-dasharray: 2 3; stroke-width: 2.5px;`
		: conditionLabel
			? `${color}stroke-dasharray: 5 4;`
			: '';
	if (highlighted) {
		style = `${style} stroke: var(--color-agent-cyan-600); stroke-width: 3px;`;
	}
	const labelStyle = conditionLabel
		? 'font-size: 11px; font-weight: 600; fill: var(--color-agent-magenta);'
		: label
			? 'font-size: 11px; font-weight: 600;'
			: undefined;
	return { ...(label ? { label } : {}), ...(labelStyle ? { labelStyle } : {}), style };
}

// #200: the nearest ancestor of a merge node from which 2+ of its outbound
// paths reconverge at that merge -- i.e. where the branches feeding this
// merge actually diverged. Mirrors engine.py's _resolve_merge_arrivals in
// spirit (the join fires at the nearest point of concurrency, however many
// hops each sibling branch took to get there -- see the module docstring),
// but computed statically from graph shape rather than a live run.
//
// A merge node needs 2+ direct incoming edges to have a fan-out worth
// showing at all -- a single incoming edge isn't a join. From there: BFS
// backward from the merge collects every ancestor with a forward path to
// it, in increasing-distance order, so the first node encountered with 2+
// outbound edges landing back in that set is the *nearest* fan-out point,
// not just any fan-out point further upstream. Everything reachable
// forward from that ancestor and backward-reachable from the merge is on
// some path between them -- same BFS-over-adjacency style as isLoopEdge,
// deliberately not a full graph-theory LCA pass (small canvases, recomputed
// on selection change, not per frame).
export function findMergeFanOut(
	connections: WorkflowConnection[],
	mergeNodeId: string
): MergeFanOut | null {
	const directPreds = connections.filter((c) => c.to_node_id === mergeNodeId && c.from_node_id);
	if (directPreds.length < 2) return null;

	const forward = new Map<string, string[]>();
	const backward = new Map<string, string[]>();
	for (const c of connections) {
		if (!c.from_node_id) continue;
		const outbound = forward.get(c.from_node_id) ?? [];
		outbound.push(c.to_node_id);
		forward.set(c.from_node_id, outbound);
		const inbound = backward.get(c.to_node_id) ?? [];
		inbound.push(c.from_node_id);
		backward.set(c.to_node_id, inbound);
	}

	const seen = new Set([mergeNodeId]);
	const bfsOrder: string[] = [];
	const queue = [mergeNodeId];
	while (queue.length > 0) {
		const current = queue.shift()!;
		for (const from of backward.get(current) ?? []) {
			if (!seen.has(from)) {
				seen.add(from);
				bfsOrder.push(from);
				queue.push(from);
			}
		}
	}
	// Everything that can reach the merge node, plus the merge node itself.
	const reachSet = seen;

	let ancestorId: string | null = null;
	let branchCount = 0;
	for (const candidate of bfsOrder) {
		const branchesTowardMerge = (forward.get(candidate) ?? []).filter((to) => reachSet.has(to));
		if (branchesTowardMerge.length >= 2) {
			ancestorId = candidate;
			branchCount = branchesTowardMerge.length;
			break;
		}
	}
	if (!ancestorId) return null;

	// Forward reachability from the ancestor, intersected with reachSet,
	// is exactly the nodes on some path from the fan-out point to the
	// merge -- regardless of how many hops each branch takes.
	const nodeIds = new Set<string>([ancestorId]);
	const fq = [ancestorId];
	while (fq.length > 0) {
		const current = fq.shift()!;
		for (const to of forward.get(current) ?? []) {
			if (reachSet.has(to) && !nodeIds.has(to)) {
				nodeIds.add(to);
				fq.push(to);
			}
		}
	}

	const edgeIds = new Set(
		connections
			.filter((c) => c.from_node_id && nodeIds.has(c.from_node_id) && nodeIds.has(c.to_node_id))
			.map((c) => c.id)
	);

	return { ancestorId, branchCount, nodeIds, edgeIds };
}

// Pure WorkflowNode[]/WorkflowConnection[] -> Svelte Flow {nodes, edges}
// transform. Framework-agnostic on purpose so branch/merge correctness is
// unit-testable without any DOM/browser involvement. Replaces the old
// buildChain(), which walked one outbound connection per node via a Map and
// silently dropped every branch past the first.
export function buildFlowGraph(
	nodes: WorkflowNode[],
	connections: WorkflowConnection[],
	subtitleFor: (node: WorkflowNode) => string | null,
	// #200: the currently-selected node id, if any -- only used to compute
	// mergeFanOut/highlighting when it names an actual merge node. Passing
	// a non-merge node id (or omitting it) leaves every node/edge exactly
	// as buildFlowGraph produced them before #200.
	selectedNodeId?: string | null
): BuildFlowGraphResult {
	const nodeIds = new Set(nodes.map((n) => n.id));
	const entryNodeId = connections.find((c) => c.from_node_id === null)?.to_node_id ?? null;

	const selectedNode = selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) : undefined;
	const mergeFanOut =
		selectedNode?.node_type === 'merge' ? findMergeFanOut(connections, selectedNode.id) : null;

	const flowNodes: WorkflowFlowNode[] = nodes.map((node) => ({
		id: node.id,
		type: 'workflowNode',
		position: { x: node.position_x ?? 0, y: node.position_y ?? 0 },
		data: {
			label: node.name,
			nodeType: node.node_type,
			subtitle: subtitleFor(node),
			isEntry: node.id === entryNodeId,
			mergeHighlight: !mergeFanOut
				? null
				: node.id === mergeFanOut.ancestorId
					? 'ancestor'
					: mergeFanOut.nodeIds.has(node.id)
						? 'branch'
						: null
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
	let hasLoop = false;
	const flowEdges: Edge[] = connections
		.filter(
			(c): c is WorkflowConnection & { from_node_id: string } =>
				c.from_node_id !== null && nodeIds.has(c.from_node_id) && nodeIds.has(c.to_node_id)
		)
		.map((c) => {
			const conditionLabel = conditionEdgeLabel(c.condition_json);
			const loop = isLoopEdge(connections, c);
			if (loop) hasLoop = true;
			const highlighted = mergeFanOut?.edgeIds.has(c.id) ?? false;
			return {
				id: c.id,
				source: c.from_node_id,
				target: c.to_node_id,
				type: 'smoothstep',
				// Lets tests target a specific edge (`page.getByTestId(...)`) the
				// same way workflow-node-${id} does for nodes -- domAttributes is
				// spread straight onto the rendered <g class="svelte-flow__edge">.
				domAttributes: {
					'data-testid': `workflow-edge-${c.id}`,
					'data-merge-highlight': highlighted ? 'true' : undefined
				},
				// A conditional and/or loop edge gets a label plus a distinct
				// stroke so branching/looping is visible on the canvas itself,
				// not just in the inspector -- a plain edge keeps the solid line
				// it always had (no extra keys, so existing snapshots/tests for
				// those are untouched). #200 layers a highlight on top when this
				// edge is on the selected merge node's fan-out path.
				...(edgeVisual(conditionLabel, loop, highlighted) ?? {})
			};
		});

	return { nodes: flowNodes, edges: flowEdges, entryNodeId, hasLoop, mergeFanOut };
}
