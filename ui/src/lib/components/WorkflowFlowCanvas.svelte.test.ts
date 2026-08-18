import { page } from 'vitest/browser';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowFlowCanvas from './WorkflowFlowCanvas.svelte';
import { PALETTE_DRAG_MIME } from './WorkflowNodePalette.svelte';
import type { WorkflowFlowNode } from '$lib/workflowFlowGraph';

const node: WorkflowFlowNode = {
	id: 'node-1',
	type: 'workflowNode',
	position: { x: 40, y: 40 },
	data: {
		label: 'Research',
		nodeType: 'agent',
		subtitle: 'Writer',
		isEntry: true,
		mergeHighlight: null,
		childWorkflowId: null,
		runStatus: null,
		runOverlayActive: false
	}
};

describe('WorkflowFlowCanvas.svelte', () => {
	it('shows the palette unless the board is read-only', async () => {
		render(WorkflowFlowCanvas, {
			nodes: [node],
			edges: [],
			colorMode: 'light',
			onnodeclick: vi.fn(),
			onnodesmoved: vi.fn(),
			onpalettedrop: vi.fn(),
			onconnect: vi.fn(),
			onedgeclick: vi.fn()
		});

		await expect.element(page.getByTestId('palette-node-agent')).toBeInTheDocument();
		await expect.element(page.getByTestId('workflow-canvas')).toBeInTheDocument();
	});

	it('hides the palette when read-only', async () => {
		render(WorkflowFlowCanvas, {
			nodes: [node],
			edges: [],
			colorMode: 'light',
			onnodeclick: vi.fn(),
			onnodesmoved: vi.fn(),
			onpalettedrop: vi.fn(),
			onconnect: vi.fn(),
			onedgeclick: vi.fn(),
			readOnly: true
		});

		await expect.element(page.getByTestId('palette-node-agent')).not.toBeInTheDocument();
	});

	it('forwards a palette drop onto the canvas', async () => {
		const onpalettedrop = vi.fn();
		render(WorkflowFlowCanvas, {
			nodes: [],
			edges: [],
			colorMode: 'light',
			onnodeclick: vi.fn(),
			onnodesmoved: vi.fn(),
			onpalettedrop,
			onconnect: vi.fn(),
			onedgeclick: vi.fn()
		});

		const canvas = page.getByTestId('workflow-canvas');
		await expect.element(canvas).toBeInTheDocument();

		const dt = new DataTransfer();
		dt.setData(PALETTE_DRAG_MIME, 'transform');
		canvas
			.element()
			.dispatchEvent(
				new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt })
			);
		canvas.element().dispatchEvent(
			new DragEvent('drop', {
				bubbles: true,
				cancelable: true,
				dataTransfer: dt,
				clientX: 120,
				clientY: 80
			})
		);

		expect(onpalettedrop).toHaveBeenCalledWith('transform', expect.any(Object));
	});
});
