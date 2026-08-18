import { page } from 'vitest/browser';
import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowNodePalette, { PALETTE_DRAG_MIME } from './WorkflowNodePalette.svelte';

describe('WorkflowNodePalette.svelte', () => {
	it('renders every step type in plain language', async () => {
		render(WorkflowNodePalette);

		await expect.element(page.getByTestId('palette-node-agent')).toBeInTheDocument();
		await expect.element(page.getByTestId('palette-node-conditional')).toHaveTextContent('If');
		await expect
			.element(page.getByTestId('palette-node-human_input'))
			.toHaveTextContent('Wait for a person');
	});

	it('stashes the node type on the drag payload', async () => {
		render(WorkflowNodePalette);
		const chip = page.getByTestId('palette-node-agent');
		await expect.element(chip).toBeInTheDocument();

		const dt = new DataTransfer();
		chip
			.element()
			.dispatchEvent(
				new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt })
			);

		expect(dt.getData(PALETTE_DRAG_MIME)).toBe('agent');
	});
});
