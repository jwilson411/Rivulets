// Browser-mode component test (see channels/[id]/channelPage.svelte.test.ts
// for the $app/state + $app/paths mocking pattern this route needs, since
// it reads its workflow id from page.params.id). $lib/format's timeAgo is
// real/unmocked -- no network I/O, safe to exercise as-is.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowBuilderPage from './+page.svelte';
import {
	workflows,
	type Workflow,
	type WorkflowNode,
	type WorkflowConnection
} from '$lib/api/workflows';
import { agents } from '$lib/api/agents';

vi.mock('$app/state', () => ({
	page: { params: { id: 'wf-1' }, url: new URL('http://localhost/workflows/wf-1') }
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		let out = path;
		if (params) {
			for (const [key, value] of Object.entries(params)) out = out.replace(`[${key}]`, value);
		}
		return out;
	}
}));

vi.mock('$lib/api/workflows', () => ({
	workflows: {
		get: vi.fn(),
		list: vi.fn(),
		update: vi.fn(),
		publish: vi.fn(),
		unpublish: vi.fn(),
		listNodes: vi.fn(),
		createNode: vi.fn(),
		updateNode: vi.fn(),
		removeNode: vi.fn(),
		listConnections: vi.fn(),
		createConnection: vi.fn(),
		removeConnection: vi.fn(),
		listRuns: vi.fn(),
		listNodeRuns: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

const reviewFlow: Workflow = {
	id: 'wf-1',
	name: 'review-pr',
	description: 'Runs a PR through review',
	published: true,
	created_at: '2026-08-01T00:00:00Z',
	updated_at: '2026-08-01T00:00:00Z'
};

const fetchNode: WorkflowNode = {
	id: 'n1',
	workflow_id: 'wf-1',
	name: 'Fetch',
	node_type: 'agent',
	agent_id: 'agent-1',
	child_workflow_id: null,
	config: {},
	retry_max_attempts: 0,
	retry_backoff_seconds: 5
};

const formatNode: WorkflowNode = {
	id: 'n2',
	workflow_id: 'wf-1',
	name: 'Format',
	node_type: 'transform',
	agent_id: null,
	child_workflow_id: null,
	config: { template: '{input}!' },
	retry_max_attempts: 0,
	retry_backoff_seconds: 5
};

const entryConnection: WorkflowConnection = {
	id: 'c1',
	workflow_id: 'wf-1',
	from_node_id: null,
	to_node_id: 'n1'
};

const chainConnection: WorkflowConnection = {
	id: 'c2',
	workflow_id: 'wf-1',
	from_node_id: 'n1',
	to_node_id: 'n2'
};

function mockLoad() {
	vi.mocked(workflows.get).mockResolvedValue(reviewFlow);
	vi.mocked(workflows.list).mockResolvedValue([reviewFlow]);
	vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode]);
	vi.mocked(workflows.listConnections).mockResolvedValue([entryConnection, chainConnection]);
	vi.mocked(agents.list).mockResolvedValue([
		{
			id: 'agent-1',
			name: 'Researcher',
			description: 'd',
			instructions: 'i',
			model: 'm',
			agentos_agent_id: null
		}
	]);
}

afterEach(() => {
	vi.clearAllMocks();
});

describe('workflows/[id]/+page.svelte', () => {
	it('renders the chain in connection order', async () => {
		mockLoad();

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('1. Agent')).toBeInTheDocument();
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();
		await expect.element(page.getByText('2. Transform')).toBeInTheDocument();
		await expect.element(page.getByText('Format')).toBeInTheDocument();
	});

	it('inserts a new step between two existing steps, rewiring the connection around it', async () => {
		mockLoad();
		vi.mocked(workflows.createNode).mockResolvedValueOnce({
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Recap',
			node_type: 'summarize',
			agent_id: null,
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Button order: top "+ Add step", then one after each chain node --
		// index 1 is the one between Fetch (n1) and Format (n2).
		await page.getByRole('button', { name: '+ Add step' }).nth(1).click();
		await page.getByPlaceholder('Step name').fill('Recap');
		await page.getByRole('combobox').first().selectOptions('summarize');
		await page.getByRole('button', { name: 'Add step', exact: true }).click();

		expect(workflows.createNode).toHaveBeenCalledWith('wf-1', {
			name: 'Recap',
			node_type: 'summarize',
			agent_id: null,
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
		expect(workflows.removeConnection).toHaveBeenCalledWith('wf-1', 'c2');
		expect(workflows.createConnection).toHaveBeenNthCalledWith(1, 'wf-1', {
			from_node_id: 'n1',
			to_node_id: 'n3'
		});
		expect(workflows.createConnection).toHaveBeenNthCalledWith(2, 'wf-1', {
			from_node_id: 'n3',
			to_node_id: 'n2'
		});
	});

	it('inserts a nested workflow step, excluding the current workflow from the picker', async () => {
		mockLoad();
		const otherFlow: Workflow = { ...reviewFlow, id: 'wf-2', name: 'other-flow' };
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow, otherFlow]);
		vi.mocked(workflows.createNode).mockResolvedValueOnce({
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Invoke other',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add step' }).first().click();
		await page.getByPlaceholder('Step name').fill('Invoke other');
		await page.getByRole('combobox').first().selectOptions('workflow');

		const workflowPicker = page.getByRole('combobox').nth(1);
		await expect
			.element(workflowPicker.getByRole('option', { name: '/other-flow' }))
			.toBeInTheDocument();
		await expect
			.element(workflowPicker.getByRole('option', { name: '/review-pr' }))
			.not.toBeInTheDocument();
		await workflowPicker.selectOptions('wf-2');
		await page.getByRole('button', { name: 'Add step', exact: true }).click();

		expect(workflows.createNode).toHaveBeenCalledWith('wf-1', {
			name: 'Invoke other',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});

	it('removes a step and reconnects its neighbors', async () => {
		mockLoad();
		vi.mocked(workflows.removeNode).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).first().click();

		expect(workflows.removeNode).toHaveBeenCalledWith('wf-1', 'n1');
		expect(workflows.createConnection).toHaveBeenCalledWith('wf-1', {
			from_node_id: null,
			to_node_id: 'n2'
		});
	});

	it('renames the workflow', async () => {
		mockLoad();
		vi.mocked(workflows.update).mockResolvedValueOnce({ ...reviewFlow, name: 'review-pr-v2' });

		render(WorkflowBuilderPage);
		await expect.element(page.getByRole('heading', { name: /review-pr/ })).toBeInTheDocument();

		await page.getByRole('banner').getByRole('button', { name: 'Edit' }).click();
		const nameInputs = page.getByRole('textbox');
		await nameInputs.first().fill('review-pr-v2');
		await page.getByRole('button', { name: 'Save' }).click();

		expect(workflows.update).toHaveBeenCalledWith('wf-1', {
			name: 'review-pr-v2',
			description: 'Runs a PR through review'
		});
	});

	it('shows the Published badge and lets a published workflow be unpublished', async () => {
		mockLoad();
		vi.mocked(workflows.unpublish).mockResolvedValueOnce({ ...reviewFlow, published: false });

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Published')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Unpublish' }).click();

		expect(workflows.unpublish).toHaveBeenCalledWith('wf-1');
		await expect.element(page.getByText('Draft')).toBeInTheDocument();
	});

	it('publishes a draft workflow and surfaces a rejection', async () => {
		mockLoad();
		vi.mocked(workflows.get).mockResolvedValue({ ...reviewFlow, published: false });
		vi.mocked(workflows.publish).mockRejectedValueOnce(
			new Error('Workflow has no entry point yet — connect a first step before publishing')
		);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Draft')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Publish' }).click();

		expect(workflows.publish).toHaveBeenCalledWith('wf-1');
		await expect
			.element(page.getByText(/connect a first step before publishing/))
			.toBeInTheDocument();
	});

	it('loads and expands run history', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-1',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-1',
				triggered_by: 'human',
				triggered_by_id: null,
				status: 'completed',
				current_node_id: null,
				error_message: null,
				final_output: 'fetched stuff',
				started_at: '2026-08-01T00:00:00Z',
				completed_at: '2026-08-01T00:01:00Z'
			}
		]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValueOnce([
			{
				id: 'nr-1',
				node_id: 'n1',
				attempt: 1,
				status: 'completed',
				output_content: 'fetched stuff',
				error_message: null,
				started_at: '2026-08-01T00:00:00Z',
				completed_at: '2026-08-01T00:00:30Z'
			}
		]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();
		await expect.element(page.getByText('completed')).toBeInTheDocument();

		await page.getByText('completed').first().click();
		await expect.element(page.getByText('fetched stuff')).toBeInTheDocument();
	});
});
