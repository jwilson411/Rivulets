import { describe, expect, it } from 'vitest';
import { buildFlowGraph, conditionEdgeLabel, isLoopEdge } from './workflowFlowGraph';
import type { WorkflowConnection, WorkflowNode } from './api/workflows';

function node(overrides: Partial<WorkflowNode> & { id: string }): WorkflowNode {
	return {
		workflow_id: 'w1',
		name: overrides.id,
		node_type: 'agent',
		agent_id: null,
		child_workflow_id: null,
		config: {},
		retry_max_attempts: 0,
		retry_backoff_seconds: 5,
		position_x: 0,
		position_y: 0,
		...overrides
	};
}

function connection(
	overrides: Partial<WorkflowConnection> & { id: string; to_node_id: string }
): WorkflowConnection {
	return {
		workflow_id: 'w1',
		from_node_id: null,
		condition_json: null,
		...overrides
	};
}

const noSubtitle = () => null;

describe('buildFlowGraph', () => {
	it('renders a linear two-node chain as one edge with the entry node marked', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const entry = connection({ id: 'c-entry', from_node_id: null, to_node_id: 'n1' });
		const chain = connection({ id: 'c-chain', from_node_id: 'n1', to_node_id: 'n2' });

		const result = buildFlowGraph([n1, n2], [entry, chain], noSubtitle);

		expect(result.entryNodeId).toBe('n1');
		expect(result.nodes).toHaveLength(2);
		expect(result.nodes.find((n) => n.id === 'n1')?.data.isEntry).toBe(true);
		expect(result.nodes.find((n) => n.id === 'n2')?.data.isEntry).toBe(false);

		// The entry connection has no source node -- it must never become an edge.
		expect(result.edges).toEqual([
			{
				id: 'c-chain',
				source: 'n1',
				target: 'n2',
				type: 'smoothstep',
				domAttributes: { 'data-testid': 'workflow-edge-c-chain' }
			}
		]);
	});

	it('renders branching as two distinct edges sharing a source', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const n3 = node({ id: 'n3' });
		const branchA = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' });
		const branchB = connection({ id: 'c-b', from_node_id: 'n1', to_node_id: 'n3' });

		const result = buildFlowGraph([n1, n2, n3], [branchA, branchB], noSubtitle);

		expect(result.edges).toHaveLength(2);
		expect(result.edges.map((e) => e.source)).toEqual(['n1', 'n1']);
		expect(result.edges.map((e) => e.target).sort()).toEqual(['n2', 'n3']);
	});

	it('renders a merge as two distinct edges sharing a target', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const n3 = node({ id: 'n3' });
		const intoMerge1 = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n3' });
		const intoMerge2 = connection({ id: 'c-b', from_node_id: 'n2', to_node_id: 'n3' });

		const result = buildFlowGraph([n1, n2, n3], [intoMerge1, intoMerge2], noSubtitle);

		expect(result.edges).toHaveLength(2);
		expect(result.edges.map((e) => e.target)).toEqual(['n3', 'n3']);
		expect(result.edges.map((e) => e.source).sort()).toEqual(['n1', 'n2']);
	});

	it('includes an orphan node with no edges referencing it', () => {
		const n1 = node({ id: 'n1' });
		const orphan = node({ id: 'orphan' });
		const entry = connection({ id: 'c-entry', from_node_id: null, to_node_id: 'n1' });

		const result = buildFlowGraph([n1, orphan], [entry], noSubtitle);

		expect(result.nodes.map((n) => n.id).sort()).toEqual(['n1', 'orphan']);
		expect(result.edges).toEqual([]);
	});

	it('drops a connection referencing a node that no longer exists, without throwing', () => {
		const n1 = node({ id: 'n1' });
		const dangling = connection({ id: 'c-dangling', from_node_id: 'n1', to_node_id: 'ghost' });

		expect(() => buildFlowGraph([n1], [dangling], noSubtitle)).not.toThrow();
		const result = buildFlowGraph([n1], [dangling], noSubtitle);
		expect(result.nodes).toHaveLength(1);
		expect(result.edges).toEqual([]);
	});

	it('reports no entry node and no crash when there is no entry connection', () => {
		const n1 = node({ id: 'n1' });

		const result = buildFlowGraph([n1], [], noSubtitle);

		expect(result.entryNodeId).toBeNull();
		expect(result.nodes[0].data.isEntry).toBe(false);
	});

	it('defaults a null position to the origin', () => {
		const n1 = node({ id: 'n1', position_x: null, position_y: null });

		const result = buildFlowGraph([n1], [], noSubtitle);

		expect(result.nodes[0].position).toEqual({ x: 0, y: 0 });
	});

	it('passes each node through the subtitleFor callback', () => {
		const n1 = node({ id: 'n1', name: 'Fetch data' });

		const result = buildFlowGraph([n1], [], (n) => `subtitle for ${n.name}`);

		expect(result.nodes[0].data.subtitle).toBe('subtitle for Fetch data');
		expect(result.nodes[0].data.label).toBe('Fetch data');
	});

	it('leaves an unconditional edge with no label or style', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const plain = connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' });

		const result = buildFlowGraph([n1, n2], [plain], noSubtitle);

		expect(result.edges[0].label).toBeUndefined();
		expect(result.edges[0].style).toBeUndefined();
	});

	it('labels and styles a conditional edge distinctly from a plain one', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const conditional = connection({
			id: 'c1',
			from_node_id: 'n1',
			to_node_id: 'n2',
			condition_json: { contains: 'urgent' }
		});

		const result = buildFlowGraph([n1, n2], [conditional], noSubtitle);

		expect(result.edges[0].label).toBe('contains "urgent"');
		expect(result.edges[0].style).toContain('stroke-dasharray');
	});

	it('reports hasLoop false and leaves a forward-only edge unstyled', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const forward = connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' });

		const result = buildFlowGraph([n1, n2], [forward], noSubtitle);

		expect(result.hasLoop).toBe(false);
		expect(result.edges[0].label).toBeUndefined();
		expect(result.edges[0].style).toBeUndefined();
	});

	it('marks a back edge that closes a cycle as a loop, with a distinct label and style', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const forward = connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' });
		const back = connection({ id: 'c2', from_node_id: 'n2', to_node_id: 'n1' });

		const result = buildFlowGraph([n1, n2], [forward, back], noSubtitle);

		expect(result.hasLoop).toBe(true);
		const backEdge = result.edges.find((e) => e.id === 'c2');
		expect(backEdge?.label).toBe('↻ loop back');
		expect(backEdge?.style).toContain('stroke-dasharray');
		// In a two-node cycle both edges close the loop symmetrically -- n2
		// (c1's target) can reach n1 via c2, and n1 (c2's target) can reach n2
		// via c1 -- so both are marked, not just whichever was drawn last.
		const forwardEdge = result.edges.find((e) => e.id === 'c1');
		expect(forwardEdge?.label).toBe('↻ loop back');
	});

	it('combines the loop marker with a condition label when a loop edge is also conditional', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const forward = connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' });
		const back = connection({
			id: 'c2',
			from_node_id: 'n2',
			to_node_id: 'n1',
			condition_json: { contains: 'retry' }
		});

		const result = buildFlowGraph([n1, n2], [forward, back], noSubtitle);

		const backEdge = result.edges.find((e) => e.id === 'c2');
		expect(backEdge?.label).toBe('↻ contains "retry"');
		expect(backEdge?.style).toContain('var(--color-agent-magenta)');
	});
});

describe('isLoopEdge', () => {
	it('is false for an edge with no path back from its target', () => {
		const connections = [
			connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c2', from_node_id: 'n2', to_node_id: 'n3' })
		];

		expect(isLoopEdge(connections, connections[0])).toBe(false);
		expect(isLoopEdge(connections, connections[1])).toBe(false);
	});

	it('is true for an edge that closes a two-node cycle', () => {
		const connections = [
			connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c2', from_node_id: 'n2', to_node_id: 'n1' })
		];

		expect(isLoopEdge(connections, connections[1])).toBe(true);
	});

	it('is true for a node looping back to itself', () => {
		const connections = [connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n1' })];

		expect(isLoopEdge(connections, connections[0])).toBe(true);
	});

	it('is true for an edge closing a longer cycle through several nodes', () => {
		const connections = [
			connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c2', from_node_id: 'n2', to_node_id: 'n3' }),
			connection({ id: 'c3', from_node_id: 'n3', to_node_id: 'n1' })
		];

		expect(isLoopEdge(connections, connections[2])).toBe(true);
		// Every edge in the cycle closes it back onto some earlier node in the
		// walk, so all three are loop edges, not just the one that happens to
		// point at the original entry.
		expect(isLoopEdge(connections, connections[0])).toBe(true);
	});
});

describe('conditionEdgeLabel', () => {
	it('returns null for no condition', () => {
		expect(conditionEdgeLabel(null)).toBeNull();
	});

	it('labels a contains condition', () => {
		expect(conditionEdgeLabel({ contains: 'urgent' })).toBe('contains "urgent"');
	});

	it('labels a not_contains condition', () => {
		expect(conditionEdgeLabel({ not_contains: 'spam' })).toBe('not contains "spam"');
	});

	it('returns null for a malformed shape', () => {
		expect(conditionEdgeLabel({ contains: 'x', extra: 1 })).toBeNull();
	});
});
