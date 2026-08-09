// Browser-mode component test (see AgentForm.svelte.test.ts) -- covers the
// per-node-type config fields, the validation guards in handleSubmit, and
// the lockNodeType / error / no-oncancel display states.

import { page } from 'vitest/browser';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowNodeForm from './WorkflowNodeForm.svelte';
import type { Agent } from '$lib/api/agents';
import type { Workflow } from '$lib/api/workflows';

const agentOptions: Agent[] = [
	{
		id: 'agent-1',
		name: 'Researcher',
		description: 'Looks things up',
		instructions: 'Be thorough',
		model: 'anthropic:claude-haiku-4-5-20251001',
		agentos_agent_id: null
	}
];

const workflowOptions: Workflow[] = [];

describe('WorkflowNodeForm.svelte', () => {
	it('does not submit when the name is blank', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).not.toHaveBeenCalled();
	});

	it('does not submit an agent step without an agent selected', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('Research step');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).not.toHaveBeenCalled();
	});

	it('submits a trimmed agent step with the selected agent', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('  Research step  ');
		await page.getByRole('combobox').nth(1).selectOptions('agent-1');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).toHaveBeenCalledWith({
			name: 'Research step',
			node_type: 'agent',
			agent_id: 'agent-1',
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});

	it('submits a transform step with a template config', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('Format step');
		await page.getByRole('combobox').first().selectOptions('transform');
		await page.getByPlaceholder(/Template/).fill('{input}!');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).toHaveBeenCalledWith({
			name: 'Format step',
			node_type: 'transform',
			agent_id: null,
			child_workflow_id: null,
			config: { template: '{input}!' },
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});

	it('submits a transform step with an empty config when the template is blank', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('Format step');
		await page.getByRole('combobox').first().selectOptions('transform');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).toHaveBeenCalledWith(
			expect.objectContaining({ node_type: 'transform', config: {} })
		);
	});

	it('submits a conditional step with a contains config', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('Gate step');
		await page.getByRole('combobox').first().selectOptions('conditional');
		await page.getByPlaceholder(/Stop the run unless/).fill('approved');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).toHaveBeenCalledWith(
			expect.objectContaining({ node_type: 'conditional', config: { contains: 'approved' } })
		);
	});

	it('shows a no-configuration hint for summarize and merge and human_input steps', async () => {
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit: vi.fn()
		});

		await page.getByRole('combobox').first().selectOptions('summarize');
		await expect.element(page.getByText('no configuration needed')).toBeInTheDocument();

		await page.getByRole('combobox').first().selectOptions('merge');
		await expect.element(page.getByText(/Combines every branch/)).toBeInTheDocument();

		await page.getByRole('combobox').first().selectOptions('human_input');
		await expect.element(page.getByText(/Pauses the run/)).toBeInTheDocument();
	});

	it('locks the node type to a fixed label when lockNodeType is set', async () => {
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			initial: {
				name: 'Existing step',
				node_type: 'transform',
				agent_id: null,
				child_workflow_id: null,
				config: {},
				retry_max_attempts: 0,
				retry_backoff_seconds: 5
			},
			lockNodeType: true,
			submitLabel: 'Save',
			busyLabel: 'Saving…',
			onsubmit: vi.fn()
		});

		await expect.element(page.getByText('Transform')).toBeInTheDocument();
	});

	it('lets retries and backoff be edited and included on submit', async () => {
		const onsubmit = vi.fn();
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			onsubmit
		});

		await page.getByPlaceholder('Step name').fill('Summarize step');
		await page.getByRole('combobox').first().selectOptions('summarize');
		await page.getByLabelText('Retries').fill('3');
		await page.getByLabelText('Backoff (s)').fill('30');
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(onsubmit).toHaveBeenCalledWith(
			expect.objectContaining({ retry_max_attempts: 3, retry_backoff_seconds: 30 })
		);
	});

	it('shows the passed error message and omits Cancel when oncancel is not given', async () => {
		render(WorkflowNodeForm, {
			agentOptions,
			workflowOptions,
			submitLabel: 'Add step',
			busyLabel: 'Adding…',
			error: 'Could not save step',
			onsubmit: vi.fn()
		});

		await expect.element(page.getByText('Could not save step')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
	});
});
