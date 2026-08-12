import { describe, expect, it } from 'vitest';
import {
	buildFlowGraph,
	buildRunOverlay,
	conditionEdgeLabel,
	findMergeFanOut,
	isLoopEdge
} from './workflowFlowGraph';
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

	it('#200: leaves every node/edge unhighlighted when no node is selected', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const n3 = node({ id: 'n3', node_type: 'merge' });
		const a = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n3' });
		const b = connection({ id: 'c-b', from_node_id: 'n2', to_node_id: 'n3' });

		const result = buildFlowGraph([n1, n2, n3], [a, b], noSubtitle);

		expect(result.mergeFanOut).toBeNull();
		expect(result.nodes.every((n) => n.data.mergeHighlight === null)).toBe(true);
	});

	it('#200: leaves everything unhighlighted when the selected node is not a merge', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const n3 = node({ id: 'n3', node_type: 'merge' });
		const a = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n3' });
		const b = connection({ id: 'c-b', from_node_id: 'n2', to_node_id: 'n3' });

		const result = buildFlowGraph([n1, n2, n3], [a, b], noSubtitle, 'n1');

		expect(result.mergeFanOut).toBeNull();
		expect(result.nodes.every((n) => n.data.mergeHighlight === null)).toBe(true);
	});

	it('#200: highlights the fan-out ancestor and both branches when a diamond merge is selected', () => {
		const n1 = node({ id: 'n1' });
		const n2 = node({ id: 'n2' });
		const n3 = node({ id: 'n3' });
		const merge = node({ id: 'm', node_type: 'merge' });
		const toN2 = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' });
		const toN3 = connection({ id: 'c-b', from_node_id: 'n1', to_node_id: 'n3' });
		const n2ToMerge = connection({ id: 'c-c', from_node_id: 'n2', to_node_id: 'm' });
		const n3ToMerge = connection({ id: 'c-d', from_node_id: 'n3', to_node_id: 'm' });

		const result = buildFlowGraph(
			[n1, n2, n3, merge],
			[toN2, toN3, n2ToMerge, n3ToMerge],
			noSubtitle,
			'm'
		);

		expect(result.mergeFanOut).toEqual({
			ancestorId: 'n1',
			branchCount: 2,
			nodeIds: new Set(['n1', 'n2', 'n3', 'm']),
			edgeIds: new Set(['c-a', 'c-b', 'c-c', 'c-d'])
		});
		expect(result.nodes.find((n) => n.id === 'n1')?.data.mergeHighlight).toBe('ancestor');
		expect(result.nodes.find((n) => n.id === 'n2')?.data.mergeHighlight).toBe('branch');
		expect(result.nodes.find((n) => n.id === 'n3')?.data.mergeHighlight).toBe('branch');
		expect(result.nodes.find((n) => n.id === 'm')?.data.mergeHighlight).toBe('branch');

		for (const edgeId of ['c-a', 'c-b', 'c-c', 'c-d']) {
			const edge = result.edges.find((e) => e.id === edgeId)!;
			expect(edge.style).toContain('var(--color-agent-cyan-600)');
			expect(edge.domAttributes).toMatchObject({ 'data-merge-highlight': 'true' });
		}
	});

	it('#201: exposes childWorkflowId for a workflow node whose child workflow exists', () => {
		const n1 = node({ id: 'n1', node_type: 'workflow', child_workflow_id: 'wf-2' });

		const result = buildFlowGraph([n1], [], noSubtitle, null, () => true);

		expect(result.nodes[0].data.childWorkflowId).toBe('wf-2');
	});

	it('#201: nulls out childWorkflowId when the child workflow no longer exists', () => {
		const n1 = node({ id: 'n1', node_type: 'workflow', child_workflow_id: 'wf-missing' });

		const result = buildFlowGraph([n1], [], noSubtitle, null, () => false);

		expect(result.nodes[0].data.childWorkflowId).toBeNull();
	});

	it('#201: defaults childWorkflowExists to true when the callback is omitted', () => {
		const n1 = node({ id: 'n1', node_type: 'workflow', child_workflow_id: 'wf-2' });

		const result = buildFlowGraph([n1], [], noSubtitle);

		expect(result.nodes[0].data.childWorkflowId).toBe('wf-2');
	});

	it('#201: is null for non-workflow nodes and workflow nodes with no child selected', () => {
		const agentNode = node({ id: 'n1' });
		const unsetWorkflowNode = node({ id: 'n2', node_type: 'workflow', child_workflow_id: null });

		const result = buildFlowGraph([agentNode, unsetWorkflowNode], [], noSubtitle);

		expect(result.nodes.find((n) => n.id === 'n1')?.data.childWorkflowId).toBeNull();
		expect(result.nodes.find((n) => n.id === 'n2')?.data.childWorkflowId).toBeNull();
	});

	describe('#202: run overlay', () => {
		it('leaves runStatus null and runOverlayActive false when no run is overlaid', () => {
			const n1 = node({ id: 'n1' });

			const result = buildFlowGraph([n1], [], noSubtitle);

			expect(result.nodes[0].data.runStatus).toBeNull();
			expect(result.nodes[0].data.runOverlayActive).toBe(false);
		});

		it('marks a reached node with its overlay status and an unreached node as off-path once the run is over', () => {
			const n1 = node({ id: 'n1' });
			const n2 = node({ id: 'n2' });
			const entry = connection({ id: 'c-entry', from_node_id: null, to_node_id: 'n1' });
			const chain = connection({ id: 'c-chain', from_node_id: 'n1', to_node_id: 'n2' });
			const overlay = buildRunOverlay([{ node_id: 'n1', attempt: 1, status: 'completed' }], false);

			const result = buildFlowGraph([n1, n2], [entry, chain], noSubtitle, null, undefined, overlay);

			expect(result.nodes.find((n) => n.id === 'n1')?.data.runStatus).toBe('succeeded');
			expect(result.nodes.find((n) => n.id === 'n1')?.data.runOverlayActive).toBe(true);
			expect(result.nodes.find((n) => n.id === 'n2')?.data.runStatus).toBeNull();
			expect(result.nodes.find((n) => n.id === 'n2')?.data.runOverlayActive).toBe(true);
		});

		it('marks an unreached node as pending while the overlaid run is still in progress', () => {
			const n1 = node({ id: 'n1' });
			const n2 = node({ id: 'n2' });
			const chain = connection({ id: 'c-chain', from_node_id: 'n1', to_node_id: 'n2' });
			const overlay = buildRunOverlay([{ node_id: 'n1', attempt: 1, status: 'running' }], true);

			const result = buildFlowGraph([n1, n2], [chain], noSubtitle, null, undefined, overlay);

			expect(result.nodes.find((n) => n.id === 'n2')?.data.runStatus).toBe('pending');
		});

		it('marks an edge as on the run path only when the run reached both endpoints', () => {
			const n1 = node({ id: 'n1' });
			const n2 = node({ id: 'n2' });
			const n3 = node({ id: 'n3' });
			const taken = connection({ id: 'c-taken', from_node_id: 'n1', to_node_id: 'n2' });
			const untaken = connection({ id: 'c-untaken', from_node_id: 'n1', to_node_id: 'n3' });
			const overlay = buildRunOverlay(
				[
					{ node_id: 'n1', attempt: 1, status: 'completed' },
					{ node_id: 'n2', attempt: 1, status: 'completed' }
				],
				false
			);

			const result = buildFlowGraph(
				[n1, n2, n3],
				[taken, untaken],
				noSubtitle,
				null,
				undefined,
				overlay
			);

			const takenEdge = result.edges.find((e) => e.id === 'c-taken')!;
			expect(takenEdge.domAttributes).toMatchObject({ 'data-run-path': 'true' });
			expect(takenEdge.style).toContain('var(--color-agent-cyan-600)');

			const untakenEdge = result.edges.find((e) => e.id === 'c-untaken')!;
			expect(untakenEdge.domAttributes).toMatchObject({ 'data-run-path': 'false' });
			expect(untakenEdge.style).toContain('opacity: 0.3');
		});

		it('skips merge fan-out highlighting while a run is overlaid, even for a selected merge node', () => {
			const n1 = node({ id: 'n1' });
			const n2 = node({ id: 'n2' });
			const n3 = node({ id: 'n3' });
			const merge = node({ id: 'm', node_type: 'merge' });
			const toN2 = connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' });
			const toN3 = connection({ id: 'c-b', from_node_id: 'n1', to_node_id: 'n3' });
			const n2ToMerge = connection({ id: 'c-c', from_node_id: 'n2', to_node_id: 'm' });
			const n3ToMerge = connection({ id: 'c-d', from_node_id: 'n3', to_node_id: 'm' });
			const overlay = buildRunOverlay([], false);

			const result = buildFlowGraph(
				[n1, n2, n3, merge],
				[toN2, toN3, n2ToMerge, n3ToMerge],
				noSubtitle,
				'm',
				undefined,
				overlay
			);

			expect(result.mergeFanOut).toBeNull();
			expect(result.nodes.every((n) => n.data.mergeHighlight === null)).toBe(true);
		});
	});
});

describe('buildRunOverlay', () => {
	it('keeps only the latest attempt per node', () => {
		const overlay = buildRunOverlay(
			[
				{ node_id: 'n1', attempt: 1, status: 'failed' },
				{ node_id: 'n1', attempt: 2, status: 'completed' }
			],
			false
		);

		expect(overlay.nodeStatus.get('n1')).toBe('succeeded');
	});

	it('maps every known raw status onto the overlay vocabulary', () => {
		const overlay = buildRunOverlay(
			[
				{ node_id: 'n1', attempt: 1, status: 'running' },
				{ node_id: 'n2', attempt: 1, status: 'completed' },
				{ node_id: 'n3', attempt: 1, status: 'failed' },
				{ node_id: 'n4', attempt: 1, status: 'skipped' },
				{ node_id: 'n5', attempt: 1, status: 'awaiting_human' }
			],
			false
		);

		expect(overlay.nodeStatus.get('n1')).toBe('running');
		expect(overlay.nodeStatus.get('n2')).toBe('succeeded');
		expect(overlay.nodeStatus.get('n3')).toBe('failed');
		expect(overlay.nodeStatus.get('n4')).toBe('skipped');
		expect(overlay.nodeStatus.get('n5')).toBe('awaiting_human');
	});

	it('carries the inProgress flag through unchanged', () => {
		expect(buildRunOverlay([], true).inProgress).toBe(true);
		expect(buildRunOverlay([], false).inProgress).toBe(false);
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

describe('findMergeFanOut', () => {
	it('returns null when the merge has fewer than two direct incoming edges', () => {
		const connections = [connection({ id: 'c1', from_node_id: 'n1', to_node_id: 'm' })];

		expect(findMergeFanOut(connections, 'm')).toBeNull();
	});

	it('returns null when a merge is targeted with no incoming edges at all', () => {
		expect(findMergeFanOut([], 'm')).toBeNull();
	});

	it('finds the direct fan-out ancestor of a simple diamond', () => {
		const connections = [
			connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c-b', from_node_id: 'n1', to_node_id: 'n3' }),
			connection({ id: 'c-c', from_node_id: 'n2', to_node_id: 'm' }),
			connection({ id: 'c-d', from_node_id: 'n3', to_node_id: 'm' })
		];

		const result = findMergeFanOut(connections, 'm');

		expect(result?.ancestorId).toBe('n1');
		expect(result?.branchCount).toBe(2);
		expect(result?.nodeIds).toEqual(new Set(['n1', 'n2', 'n3', 'm']));
		expect(result?.edgeIds).toEqual(new Set(['c-a', 'c-b', 'c-c', 'c-d']));
	});

	it('finds the same ancestor when one branch takes an extra hop the other skips', () => {
		// n1 -> n2 -> n4 (merge), n1 -> n4 directly -- unequal branch length,
		// same shape the engine docstring calls out explicitly.
		const connections = [
			connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c-b', from_node_id: 'n2', to_node_id: 'm' }),
			connection({ id: 'c-c', from_node_id: 'n1', to_node_id: 'm' })
		];

		const result = findMergeFanOut(connections, 'm');

		expect(result?.ancestorId).toBe('n1');
		expect(result?.branchCount).toBe(2);
		expect(result?.nodeIds).toEqual(new Set(['n1', 'n2', 'm']));
	});

	it('walks past a nested inner fan-out/merge to find the outer join point', () => {
		// n1 forks into n2 and n5. n2 forks again into n3/n4, which merge at
		// mInner before continuing into mOuter alongside n5. mOuter's fan-out
		// ancestor should be n1 (nearest node with 2 paths reaching mOuter),
		// not n2 (which only forks toward mInner, not mOuter directly).
		const connections = [
			connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'n2' }),
			connection({ id: 'c-b', from_node_id: 'n1', to_node_id: 'n5' }),
			connection({ id: 'c-c', from_node_id: 'n2', to_node_id: 'n3' }),
			connection({ id: 'c-d', from_node_id: 'n2', to_node_id: 'n4' }),
			connection({ id: 'c-e', from_node_id: 'n3', to_node_id: 'mInner' }),
			connection({ id: 'c-f', from_node_id: 'n4', to_node_id: 'mInner' }),
			connection({ id: 'c-g', from_node_id: 'mInner', to_node_id: 'mOuter' }),
			connection({ id: 'c-h', from_node_id: 'n5', to_node_id: 'mOuter' })
		];

		const result = findMergeFanOut(connections, 'mOuter');

		expect(result?.ancestorId).toBe('n1');
		expect(result?.branchCount).toBe(2);
		expect(result?.nodeIds).toEqual(new Set(['n1', 'n2', 'n5', 'n3', 'n4', 'mInner', 'mOuter']));
	});

	it('returns null when there is no common branching ancestor to find', () => {
		// Two disconnected incoming edges with nothing upstream that forks --
		// e.g. two separate entry points feeding the same merge.
		const connections = [
			connection({ id: 'c-a', from_node_id: 'n1', to_node_id: 'm' }),
			connection({ id: 'c-b', from_node_id: 'n2', to_node_id: 'm' })
		];

		expect(findMergeFanOut(connections, 'm')).toBeNull();
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
