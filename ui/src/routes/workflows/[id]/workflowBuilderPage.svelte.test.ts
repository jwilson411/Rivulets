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
	type WorkflowConnection,
	type WorkflowSchedule
} from '$lib/api/workflows';
import { agents } from '$lib/api/agents';
import { channels } from '$lib/api/channels';

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
		listSchedules: vi.fn(),
		createSchedule: vi.fn(),
		updateSchedule: vi.fn(),
		removeSchedule: vi.fn(),
		previewSchedule: vi.fn(),
		listRuns: vi.fn(),
		listNodeRuns: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn() }
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn() }
}));

const reviewFlow: Workflow = {
	id: 'wf-1',
	name: 'review-pr',
	description: 'Runs a PR through review',
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
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

const digestChannel = {
	id: 'ch-1',
	name: 'digest-channel',
	description: null,
	team_id: null,
	position: 0,
	archived: false
};

function mockLoad() {
	vi.mocked(workflows.get).mockResolvedValue(reviewFlow);
	vi.mocked(workflows.list).mockResolvedValue([reviewFlow]);
	vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode]);
	vi.mocked(workflows.listConnections).mockResolvedValue([entryConnection, chainConnection]);
	vi.mocked(workflows.listSchedules).mockResolvedValue([]);
	vi.mocked(channels.list).mockResolvedValue([digestChannel]);
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
		// Combobox 0 is the page-level remediation picker (#94 layer 2),
		// combobox 1 is the on-call agent picker (#94 layer 3) -- combobox
		// 2 is this form's node-type select.
		await page.getByRole('combobox').nth(2).selectOptions('summarize');
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
		// Combobox 0 is the page-level remediation picker (#94 layer 2),
		// combobox 1 is the on-call agent picker (#94 layer 3) -- combobox
		// 2 is this form's node-type select.
		await page.getByRole('combobox').nth(2).selectOptions('workflow');

		const workflowPicker = page.getByRole('combobox').nth(3);
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

	it('creates a schedule and shows it in the list', async () => {
		mockLoad();
		const created: WorkflowSchedule = {
			id: 'sched-1',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '0 9 * * *',
			run_once: false,
			input_content: 'go',
			enabled: true,
			next_fire_at: '2026-08-09T09:00:00Z',
			last_fired_at: null,
			consecutive_failures: 0,
			name: null,
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.createSchedule).mockResolvedValueOnce(created);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Initial load() already consumed mockLoad()'s `[]` default above --
		// queue the post-creation response now so it lands on the *next*
		// listSchedules call (inside handleAddSchedule), not the first one.
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([created]);

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		await page.getByPlaceholder('0 9 * * *').fill('0 9 * * *');
		await page.getByPlaceholder('input passed to the entry step').fill('go');
		await page.getByRole('button', { name: 'Add schedule', exact: true }).click();

		expect(workflows.createSchedule).toHaveBeenCalledWith('wf-1', {
			channel_id: 'ch-1',
			cron_expression: '0 9 * * *',
			input_content: 'go'
		});
		await expect.element(page.getByText('0 9 * * *')).toBeInTheDocument();
		await expect.element(page.getByText('digest-channel', { exact: false })).toBeInTheDocument();
	});

	it('shows a one-off agent-created schedule pending approval', async () => {
		mockLoad();
		const pending: WorkflowSchedule = {
			id: 'sched-pending',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: null,
			run_once: true,
			input_content: '',
			enabled: false,
			next_fire_at: '2026-08-09T09:00:00Z',
			last_fired_at: null,
			consecutive_failures: 0,
			name: 'daily digest reminder',
			created_by: 'agent-1',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([pending]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await expect
			.element(page.getByText('daily digest reminder', { exact: false }))
			.toBeInTheDocument();
		await expect
			.element(page.getByText('pending your approval', { exact: false }))
			.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Enable' })).toBeInTheDocument();
	});

	it('hides the Enable button for a one-off schedule that already fired', async () => {
		mockLoad();
		const spent: WorkflowSchedule = {
			id: 'sched-spent',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: null,
			run_once: true,
			input_content: '',
			enabled: false,
			next_fire_at: '2020-01-01T00:00:00Z',
			last_fired_at: '2020-01-01T00:05:00Z',
			consecutive_failures: 0,
			name: null,
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([spent]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await expect.element(page.getByText('once at', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Enable' })).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Disable' })).not.toBeInTheDocument();
	});

	it('shows a validation error from the preview endpoint without saving', async () => {
		mockLoad();
		vi.mocked(workflows.previewSchedule).mockResolvedValueOnce({
			valid: false,
			next_fire_at: null,
			error: 'Invalid cron expression'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		await page.getByPlaceholder('0 9 * * *').fill('nonsense');

		await expect.element(page.getByText('Invalid cron expression')).toBeInTheDocument();
		expect(workflows.createSchedule).not.toHaveBeenCalled();
	});

	it('labels a scheduled run "(scheduled)" in run history', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-2',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-2',
				triggered_by: 'schedule',
				triggered_by_id: 'sched-1',
				status: 'completed',
				current_node_id: null,
				error_message: null,
				final_output: 'digest sent',
				started_at: '2026-08-08T09:00:00Z',
				completed_at: '2026-08-08T09:00:05Z'
			}
		]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();

		await expect.element(page.getByText('(scheduled)')).toBeInTheDocument();
	});

	it('labels a remediation run "(remediation)" in run history', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-3',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-3',
				triggered_by: 'remediation',
				triggered_by_id: 'run-1',
				status: 'completed',
				current_node_id: null,
				error_message: null,
				final_output: 'recovered',
				started_at: '2026-08-08T09:05:00Z',
				completed_at: '2026-08-08T09:05:05Z'
			}
		]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();

		await expect.element(page.getByText('(remediation)')).toBeInTheDocument();
	});

	it('sets and clears the on-failure remediation workflow, including itself as an option', async () => {
		mockLoad();
		const otherFlow: Workflow = { ...reviewFlow, id: 'wf-2', name: 'other-flow' };
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow, otherFlow]);
		vi.mocked(workflows.update).mockResolvedValueOnce({
			...reviewFlow,
			on_failure_workflow_id: 'wf-1'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Unlike the nested-workflow-step picker (which excludes the
		// current workflow), remediation allows a workflow to reference
		// itself -- "retry once on failure" is a legitimate shape (#94).
		const remediationPicker = page.getByRole('combobox').first();
		await expect
			.element(remediationPicker.getByRole('option', { name: '/review-pr' }))
			.toBeInTheDocument();
		await expect
			.element(remediationPicker.getByRole('option', { name: '/other-flow' }))
			.toBeInTheDocument();

		await remediationPicker.selectOptions('wf-1');
		expect(workflows.update).toHaveBeenCalledWith('wf-1', { on_failure_workflow_id: 'wf-1' });

		vi.mocked(workflows.update).mockResolvedValueOnce({
			...reviewFlow,
			on_failure_workflow_id: null
		});
		await remediationPicker.selectOptions('');
		expect(workflows.update).toHaveBeenLastCalledWith('wf-1', { on_failure_workflow_id: null });
	});

	it('sets and clears the on-call agent, independently of remediation', async () => {
		mockLoad();
		vi.mocked(workflows.update).mockResolvedValueOnce({
			...reviewFlow,
			on_call_agent_id: 'agent-1'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Combobox 0 is the remediation picker -- combobox 1 is on-call.
		const onCallPicker = page.getByRole('combobox').nth(1);
		await expect
			.element(onCallPicker.getByRole('option', { name: 'Researcher' }))
			.toBeInTheDocument();
		await expect
			.element(onCallPicker.getByRole('option', { name: 'Workspace default' }))
			.toBeInTheDocument();

		await onCallPicker.selectOptions('agent-1');
		expect(workflows.update).toHaveBeenCalledWith('wf-1', { on_call_agent_id: 'agent-1' });

		vi.mocked(workflows.update).mockResolvedValueOnce({
			...reviewFlow,
			on_call_agent_id: null
		});
		await onCallPicker.selectOptions('');
		expect(workflows.update).toHaveBeenLastCalledWith('wf-1', { on_call_agent_id: null });
	});
});
