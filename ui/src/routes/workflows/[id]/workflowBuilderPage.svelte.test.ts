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
	type WorkflowSchedule,
	type WorkflowWebhook
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
		listWebhooks: vi.fn(),
		createWebhook: vi.fn(),
		updateWebhook: vi.fn(),
		removeWebhook: vi.fn(),
		rotateWebhookSecret: vi.fn(),
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

const namedWebhook: WorkflowWebhook = {
	id: 'wh-1',
	workflow_id: 'wf-1',
	channel_id: 'ch-1',
	name: 'GitHub',
	input_template: null,
	enabled: true,
	last_triggered_at: '2026-08-08T00:00:00Z',
	created_at: '2026-08-01T00:00:00Z',
	updated_at: '2026-08-01T00:00:00Z'
};

const unnamedWebhook: WorkflowWebhook = {
	id: 'wh-2',
	workflow_id: 'wf-1',
	channel_id: 'missing-channel',
	name: null,
	input_template: null,
	enabled: false,
	last_triggered_at: null,
	created_at: '2026-08-01T00:00:00Z',
	updated_at: '2026-08-01T00:00:00Z'
};

const orphanNode: WorkflowNode = {
	id: 'n3',
	workflow_id: 'wf-1',
	name: 'Stray',
	node_type: 'merge',
	agent_id: null,
	child_workflow_id: null,
	config: {},
	retry_max_attempts: 0,
	retry_backoff_seconds: 5
};

function mockLoad() {
	vi.mocked(workflows.get).mockResolvedValue(reviewFlow);
	vi.mocked(workflows.list).mockResolvedValue([reviewFlow]);
	vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode]);
	vi.mocked(workflows.listConnections).mockResolvedValue([entryConnection, chainConnection]);
	vi.mocked(workflows.listSchedules).mockResolvedValue([]);
	vi.mocked(workflows.listWebhooks).mockResolvedValue([]);
	vi.mocked(channels.list).mockResolvedValue([digestChannel]);
	vi.mocked(agents.list).mockResolvedValue([
		{
			id: 'agent-1',
			name: 'Researcher',
			description: 'd',
			instructions: 'i',
			model: 'm',
			fallback_models: [],
			approved_for_unattended_tools: false,
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
		// Simple mode defaults to "Every day" at 09:00, which already
		// generates '0 9 * * *' -- no need to touch the frequency/time fields.
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
		await page.getByRole('button', { name: 'Advanced (cron)' }).click();
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

	it('shows an error when the workflow fails to load', async () => {
		vi.mocked(workflows.get).mockRejectedValueOnce(new Error('network down'));
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow]);
		vi.mocked(workflows.listNodes).mockResolvedValue([]);
		vi.mocked(workflows.listConnections).mockResolvedValue([]);
		vi.mocked(workflows.listSchedules).mockResolvedValue([]);
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([]);

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('network down')).toBeInTheDocument();
	});

	it('cancels renaming without saving', async () => {
		mockLoad();

		render(WorkflowBuilderPage);
		await expect.element(page.getByRole('heading', { name: /review-pr/ })).toBeInTheDocument();

		await page.getByRole('banner').getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('textbox').first().fill('should not save');
		await page.getByPlaceholder('Description').fill('should not save either');
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(workflows.update).not.toHaveBeenCalled();
		await expect.element(page.getByRole('heading', { name: /review-pr/ })).toBeInTheDocument();
	});

	it('shows an error when renaming fails', async () => {
		mockLoad();
		vi.mocked(workflows.update).mockRejectedValueOnce(new Error('Name already taken'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByRole('heading', { name: /review-pr/ })).toBeInTheDocument();

		await page.getByRole('banner').getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('button', { name: 'Save' }).click();

		await expect.element(page.getByText('Name already taken')).toBeInTheDocument();
	});

	it('shows an error when updating the remediation workflow fails', async () => {
		mockLoad();
		vi.mocked(workflows.update).mockRejectedValueOnce(new Error('Cannot set remediation'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		const remediationPicker = page.getByRole('combobox').first();
		await remediationPicker.selectOptions('wf-1');

		await expect.element(page.getByText('Cannot set remediation')).toBeInTheDocument();
	});

	it('shows an error when updating the on-call agent fails', async () => {
		mockLoad();
		vi.mocked(workflows.update).mockRejectedValueOnce(new Error('Cannot set on-call agent'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		const onCallPicker = page.getByRole('combobox').nth(1);
		await onCallPicker.selectOptions('agent-1');

		await expect.element(page.getByText('Cannot set on-call agent')).toBeInTheDocument();
	});

	it('cancels the add-schedule form and clears the cron preview timer', async () => {
		mockLoad();

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		await page.getByRole('button', { name: 'Advanced (cron)' }).click();
		await page.getByPlaceholder('0 9 * * *').fill('0 9 * * *');
		// Clearing back to empty exercises onCronDraftChange's early-return
		// guard, since a blank cron expression has nothing to preview.
		await page.getByPlaceholder('0 9 * * *').fill('');
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(workflows.createSchedule).not.toHaveBeenCalled();
		await expect.element(page.getByPlaceholder('0 9 * * *')).not.toBeInTheDocument();
	});

	it('builds a cron expression from the simple weekly picker (#131)', async () => {
		mockLoad();
		const created: WorkflowSchedule = {
			id: 'sched-weekly',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '30 14 * * 3',
			run_once: false,
			input_content: '',
			enabled: true,
			next_fire_at: '2026-08-12T14:30:00Z',
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
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([created]);

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		// combobox 2 is Frequency; selecting "weekly" reveals a Day combobox
		// (index 3), pushing the Channel picker to index 4.
		await page.getByRole('combobox').nth(2).selectOptions('weekly');
		await page.getByRole('combobox').nth(3).selectOptions('3');
		await page.getByLabelText('Time').fill('14:30');
		await page.getByRole('button', { name: 'Add schedule', exact: true }).click();

		expect(workflows.createSchedule).toHaveBeenCalledWith('wf-1', {
			channel_id: 'ch-1',
			cron_expression: '30 14 * * 3',
			input_content: ''
		});
	});

	it('shows an error when creating a schedule fails', async () => {
		mockLoad();
		vi.mocked(workflows.createSchedule).mockRejectedValueOnce(new Error('Channel not found'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		await page.getByRole('button', { name: 'Add schedule', exact: true }).click();

		// The schedule error paragraph is shown both above the schedule list
		// and inside the open add-schedule form -- both match this text.
		await expect.element(page.getByText('Channel not found').first()).toBeInTheDocument();
	});

	it('shows the next fire time from a successful schedule preview, and supports picking a different channel', async () => {
		mockLoad();
		const secondChannel = { ...digestChannel, id: 'ch-2', name: 'alerts-channel' };
		vi.mocked(channels.list).mockResolvedValue([digestChannel, secondChannel]);
		vi.mocked(workflows.previewSchedule).mockResolvedValueOnce({
			valid: true,
			next_fire_at: '2026-08-10T09:00:00Z',
			error: null
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		// combobox 2 is the simple-mode Frequency picker added by #131;
		// the schedule's Channel picker is now combobox 3.
		await page.getByRole('combobox').nth(3).selectOptions('ch-2');

		await expect.element(page.getByText(/Next run:/)).toBeInTheDocument();
	});

	it('toggles a schedule enabled state, and surfaces an error on failure', async () => {
		mockLoad();
		const enabledSchedule: WorkflowSchedule = {
			id: 'sched-2',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '0 9 * * *',
			run_once: false,
			input_content: '',
			enabled: true,
			next_fire_at: '2026-08-09T09:00:00Z',
			last_fired_at: null,
			consecutive_failures: 0,
			name: null,
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([enabledSchedule]);
		vi.mocked(workflows.updateSchedule).mockResolvedValueOnce({
			...enabledSchedule,
			enabled: false
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Disable' }).click();
		expect(workflows.updateSchedule).toHaveBeenCalledWith('wf-1', 'sched-2', { enabled: false });
		await expect.element(page.getByRole('button', { name: 'Enable' })).toBeInTheDocument();

		vi.mocked(workflows.updateSchedule).mockRejectedValueOnce(new Error('Cannot enable schedule'));
		await page.getByRole('button', { name: 'Enable' }).click();

		await expect.element(page.getByText('Cannot enable schedule')).toBeInTheDocument();
	});

	it('removes a schedule, and surfaces an error on failure', async () => {
		mockLoad();
		const schedule: WorkflowSchedule = {
			id: 'sched-3',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '0 9 * * *',
			run_once: false,
			input_content: '',
			enabled: true,
			next_fire_at: '2026-08-09T09:00:00Z',
			last_fired_at: null,
			consecutive_failures: 0,
			name: null,
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([schedule]);
		vi.mocked(workflows.removeSchedule).mockRejectedValueOnce(new Error('Cannot remove schedule'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// The schedules section renders above the step chain, so the first
		// "Remove" button on the page belongs to this schedule, not a step.
		await page.getByRole('button', { name: 'Remove' }).first().click();
		await expect.element(page.getByText('Cannot remove schedule')).toBeInTheDocument();

		vi.mocked(workflows.removeSchedule).mockResolvedValueOnce(undefined);
		await page.getByRole('button', { name: 'Remove' }).first().click();
		expect(workflows.removeSchedule).toHaveBeenCalledWith('wf-1', 'sched-3');
	});

	it('shows schedule metadata including name, deleted channel, and failure warnings', async () => {
		mockLoad();
		const namedSchedule: WorkflowSchedule = {
			id: 'sched-4',
			workflow_id: 'wf-1',
			channel_id: 'missing-channel',
			cron_expression: '0 9 * * *',
			run_once: false,
			input_content: '',
			enabled: true,
			next_fire_at: '2026-08-09T09:00:00Z',
			last_fired_at: '2026-08-08T09:00:00Z',
			consecutive_failures: 2,
			name: 'digest',
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		const disabledAfterFailures: WorkflowSchedule = {
			id: 'sched-5',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '0 10 * * *',
			run_once: false,
			input_content: '',
			enabled: false,
			next_fire_at: '2026-08-09T10:00:00Z',
			last_fired_at: '2026-08-08T10:00:00Z',
			consecutive_failures: 5,
			name: null,
			created_by: 'human',
			created_at: '2026-08-08T00:00:00Z',
			updated_at: '2026-08-08T00:00:00Z'
		};
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([
			namedSchedule,
			disabledAfterFailures
		]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await expect.element(page.getByText('(digest)')).toBeInTheDocument();
		await expect.element(page.getByText('Deleted channel', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText(/Next run:/)).toBeInTheDocument();
		await expect
			.element(page.getByText(/2 consecutive failures/, { exact: false }))
			.toBeInTheDocument();
		await expect
			.element(page.getByText(/Disabled after 5 consecutive failures/))
			.toBeInTheDocument();
	});

	it('cancels adding a new step without submitting', async () => {
		mockLoad();

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add step' }).first().click();
		await page.getByPlaceholder('Step name').fill('abandoned');
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(workflows.createNode).not.toHaveBeenCalled();
		await expect.element(page.getByPlaceholder('Step name')).not.toBeInTheDocument();
	});

	it('adds a step at the end of the chain with no connection to rewire', async () => {
		mockLoad();
		vi.mocked(workflows.createNode).mockResolvedValueOnce({
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Wrap up',
			node_type: 'summarize',
			agent_id: null,
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Buttons: top, after Fetch (n1), after Format (n2) -- the last one
		// has no outgoing connection to rewire around.
		await page.getByRole('button', { name: '+ Add step' }).last().click();
		await page.getByPlaceholder('Step name').fill('Wrap up');
		await page.getByRole('combobox').nth(2).selectOptions('summarize');
		await page.getByRole('button', { name: 'Add step', exact: true }).click();

		expect(workflows.createConnection).toHaveBeenCalledWith('wf-1', {
			from_node_id: 'n2',
			to_node_id: 'n3'
		});
		expect(workflows.removeConnection).not.toHaveBeenCalled();
	});

	it('shows an error when adding a step fails', async () => {
		mockLoad();
		vi.mocked(workflows.createNode).mockRejectedValueOnce(new Error('Cannot add step'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add step' }).first().click();
		await page.getByPlaceholder('Step name').fill('Oops');
		await page.getByRole('combobox').nth(2).selectOptions('summarize');
		await page.getByRole('button', { name: 'Add step', exact: true }).click();

		await expect.element(page.getByText('Cannot add step')).toBeInTheDocument();
	});

	it('edits a step, supports canceling, and saves changes', async () => {
		mockLoad();
		vi.mocked(workflows.updateNode).mockResolvedValueOnce(fetchNode);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Button 0 is the header's rename Edit; button 1 is Fetch's step Edit.
		await page.getByRole('button', { name: 'Edit' }).nth(1).click();
		await page.getByRole('button', { name: 'Cancel' }).click();
		expect(workflows.updateNode).not.toHaveBeenCalled();

		await page.getByRole('button', { name: 'Edit' }).nth(1).click();
		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(workflows.updateNode).toHaveBeenCalledWith('wf-1', 'n1', {
			name: 'Fetch',
			agent_id: 'agent-1',
			child_workflow_id: undefined,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});

	it('shows an error when saving step changes fails', async () => {
		mockLoad();
		vi.mocked(workflows.updateNode).mockRejectedValueOnce(new Error('Cannot save step'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Edit' }).nth(1).click();
		await page.getByRole('button', { name: 'Save changes' }).click();

		await expect.element(page.getByText('Cannot save step')).toBeInTheDocument();
	});

	it('removes the last step in the chain without creating a reconnect', async () => {
		mockLoad();
		vi.mocked(workflows.removeNode).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Format')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).last().click();

		expect(workflows.removeNode).toHaveBeenCalledWith('wf-1', 'n2');
		expect(workflows.createConnection).not.toHaveBeenCalled();
	});

	it('shows an error when removing a step fails', async () => {
		mockLoad();
		vi.mocked(workflows.removeNode).mockRejectedValueOnce(new Error('Cannot remove step'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).first().click();

		await expect.element(page.getByText('Cannot remove step')).toBeInTheDocument();
	});

	it('shows unconnected steps and can attach one to the end of the chain', async () => {
		mockLoad();
		vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode, orphanNode]);
		vi.mocked(workflows.createConnection).mockResolvedValueOnce({
			id: 'c3',
			workflow_id: 'wf-1',
			from_node_id: 'n2',
			to_node_id: 'n3'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Unconnected steps')).toBeInTheDocument();
		await expect.element(page.getByText('Merge — Stray')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add to end of chain' }).click();

		expect(workflows.createConnection).toHaveBeenCalledWith('wf-1', {
			from_node_id: 'n2',
			to_node_id: 'n3'
		});
	});

	it('removes an unconnected step from the orphan section', async () => {
		mockLoad();
		vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode, orphanNode]);
		vi.mocked(workflows.removeNode).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Unconnected steps')).toBeInTheDocument();

		// Chain steps' "Remove" buttons render first; the orphan section's own
		// "Remove" button is last in DOM order.
		await page.getByRole('button', { name: 'Remove' }).last().click();

		expect(workflows.removeNode).toHaveBeenCalledWith('wf-1', 'n3');
	});

	it('shows an error when connecting an orphan step fails', async () => {
		mockLoad();
		vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode, orphanNode]);
		vi.mocked(workflows.createConnection).mockRejectedValueOnce(new Error('Cannot connect step'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Unconnected steps')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add to end of chain' }).click();

		await expect.element(page.getByText('Cannot connect step')).toBeInTheDocument();
	});

	it('shows the empty-chain message when there are no connections', async () => {
		mockLoad();
		vi.mocked(workflows.listConnections).mockResolvedValue([]);

		render(WorkflowBuilderPage);

		await expect
			.element(page.getByText('No steps yet — add the first one above.'))
			.toBeInTheDocument();
	});

	it('stops building the chain if a connection points to a missing node', async () => {
		mockLoad();
		vi.mocked(workflows.listConnections).mockResolvedValue([
			{ id: 'c1', workflow_id: 'wf-1', from_node_id: null, to_node_id: 'ghost' }
		]);

		render(WorkflowBuilderPage);

		await expect
			.element(page.getByText('No steps yet — add the first one above.'))
			.toBeInTheDocument();
	});

	it('renders conditional and nested-workflow step details in the chain, including deleted references', async () => {
		mockLoad();
		const conditionalNode: WorkflowNode = {
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Gate',
			node_type: 'conditional',
			agent_id: null,
			child_workflow_id: null,
			config: { contains: 'urgent' },
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		};
		const workflowNode: WorkflowNode = {
			id: 'n4',
			workflow_id: 'wf-1',
			name: 'Nested',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-missing',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		};
		const ghostAgentNode: WorkflowNode = {
			id: 'n5',
			workflow_id: 'wf-1',
			name: 'Ghost',
			node_type: 'agent',
			agent_id: 'missing-agent',
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		};
		vi.mocked(workflows.listNodes).mockResolvedValue([
			fetchNode,
			formatNode,
			conditionalNode,
			workflowNode,
			ghostAgentNode
		]);
		vi.mocked(workflows.listConnections).mockResolvedValue([
			entryConnection,
			chainConnection,
			{ id: 'c3', workflow_id: 'wf-1', from_node_id: 'n2', to_node_id: 'n3' },
			{ id: 'c4', workflow_id: 'wf-1', from_node_id: 'n3', to_node_id: 'n4' },
			{ id: 'c5', workflow_id: 'wf-1', from_node_id: 'n4', to_node_id: 'n5' }
		]);

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('stop unless input contains "urgent"')).toBeInTheDocument();
		await expect.element(page.getByText('Deleted workflow', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText('Deleted agent', { exact: false })).toBeInTheDocument();
	});

	it('shows an error when loading run history fails', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockRejectedValueOnce(new Error('Cannot load runs'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();

		await expect.element(page.getByText('Cannot load runs')).toBeInTheDocument();
	});

	it('shows an empty state when there are no runs', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();

		await expect.element(page.getByText(/No runs yet/)).toBeInTheDocument();
	});

	it('shows failed, awaiting_human and running statuses plus a nested-workflow label, and surfaces a step-history load error', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-4',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-4',
				triggered_by: 'workflow',
				triggered_by_id: 'wf-2',
				status: 'failed',
				current_node_id: null,
				error_message: 'boom',
				final_output: null,
				started_at: '2026-08-08T09:10:00Z',
				completed_at: '2026-08-08T09:10:05Z'
			},
			{
				id: 'run-5',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-5',
				triggered_by: 'human',
				triggered_by_id: null,
				status: 'awaiting_human',
				current_node_id: 'n1',
				error_message: null,
				final_output: null,
				started_at: '2026-08-08T09:15:00Z',
				completed_at: null
			},
			{
				id: 'run-6',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-6',
				triggered_by: 'human',
				triggered_by_id: null,
				status: 'running',
				current_node_id: 'n1',
				error_message: null,
				final_output: null,
				started_at: '2026-08-08T09:20:00Z',
				completed_at: null
			}
		]);
		vi.mocked(workflows.listNodeRuns).mockRejectedValueOnce(new Error('Cannot load steps'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Load runs' }).click();

		await expect.element(page.getByText('(nested)')).toBeInTheDocument();
		// exact:true avoids matching the on-failure section's unrelated
		// "...still shows as failed either way" copy.
		await expect.element(page.getByText('failed', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('awaiting_human', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('running', { exact: true })).toBeInTheDocument();

		await page.getByText('(nested)').click();
		await expect.element(page.getByText('boom')).toBeInTheDocument();
		await expect.element(page.getByText('Cannot load steps')).toBeInTheDocument();
	});

	it('collapses a run and reuses cached step history on reopen', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-7',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-7',
				triggered_by: 'human',
				triggered_by_id: null,
				status: 'completed',
				current_node_id: null,
				error_message: null,
				final_output: 'done',
				started_at: '2026-08-08T09:00:00Z',
				completed_at: '2026-08-08T09:01:00Z'
			}
		]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValueOnce([]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Load runs' }).click();

		const row = page.getByText('completed').first();
		await row.click();
		await expect.element(page.getByText('No steps recorded for this run.')).toBeInTheDocument();

		await row.click();
		await expect.element(page.getByText('No steps recorded for this run.')).not.toBeInTheDocument();

		await row.click();
		await expect.element(page.getByText('No steps recorded for this run.')).toBeInTheDocument();
		expect(workflows.listNodeRuns).toHaveBeenCalledTimes(1);
	});

	it('shows a step-level error message in run history', async () => {
		mockLoad();
		vi.mocked(workflows.listRuns).mockResolvedValueOnce([
			{
				id: 'run-8',
				workflow_id: 'wf-1',
				rivulet_id: 'riv-8',
				triggered_by: 'human',
				triggered_by_id: null,
				status: 'failed',
				current_node_id: null,
				error_message: null,
				final_output: null,
				started_at: '2026-08-08T09:00:00Z',
				completed_at: '2026-08-08T09:00:10Z'
			}
		]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValueOnce([
			{
				id: 'nr-2',
				node_id: 'n1',
				attempt: 1,
				status: 'failed',
				output_content: null,
				error_message: 'agent timed out',
				started_at: '2026-08-08T09:00:00Z',
				completed_at: '2026-08-08T09:00:05Z'
			}
		]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Load runs' }).click();

		await page.getByText('failed', { exact: true }).first().click();

		await expect.element(page.getByText('agent timed out')).toBeInTheDocument();
	});

	it('creates a webhook for a chosen channel, revealing its secret once with select-to-copy fields, and can dismiss the reveal panel', async () => {
		mockLoad();
		const secondChannel = { ...digestChannel, id: 'ch-2', name: 'alerts-channel' };
		vi.mocked(channels.list).mockResolvedValue([digestChannel, secondChannel]);
		const created = { ...namedWebhook, channel_id: 'ch-2', secret: 'shh-secret' };
		vi.mocked(workflows.createWebhook).mockResolvedValueOnce(created);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Initial load() already consumed mockLoad()'s `[]` default -- queue
		// the post-creation response for the refresh inside handleAddWebhook.
		vi.mocked(workflows.listWebhooks).mockResolvedValueOnce([created]);

		await page.getByRole('button', { name: '+ Add webhook' }).click();
		await page.getByPlaceholder('e.g. GitHub').fill('GitHub');
		await page.getByLabelText('Channel').selectOptions('ch-2');
		await page.getByRole('button', { name: 'Add webhook', exact: true }).click();

		expect(workflows.createWebhook).toHaveBeenCalledWith('wf-1', {
			channel_id: 'ch-2',
			name: 'GitHub'
		});
		await expect
			.element(page.getByText("Save this secret now — it won't be shown again."))
			.toBeInTheDocument();
		const urlField = page.getByLabelText('URL');
		const secretField = page.getByLabelText('Secret');
		await expect.element(urlField).toHaveValue(`${window.location.origin}/api/v1/webhooks/wh-1`);
		await expect.element(secretField).toHaveValue('shh-secret');
		// Clicking either readonly field selects its text for easy copying.
		await urlField.click();
		await secretField.click();

		await page.getByRole('button', { name: 'Done' }).click();
		await expect
			.element(page.getByText("Save this secret now — it won't be shown again."))
			.not.toBeInTheDocument();
	});

	it('shows an error when creating a webhook fails', async () => {
		mockLoad();
		vi.mocked(workflows.createWebhook).mockRejectedValueOnce(new Error('Cannot create webhook'));

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add webhook' }).click();
		await page.getByRole('button', { name: 'Add webhook', exact: true }).click();

		// The webhook error paragraph is shown both above the webhook list and
		// inside the open add-webhook form -- both match this text.
		await expect.element(page.getByText('Cannot create webhook').first()).toBeInTheDocument();
	});

	it('cancels adding a webhook without submitting', async () => {
		mockLoad();

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add webhook' }).click();
		await page.getByPlaceholder('e.g. GitHub').fill('abandoned');
		await page.getByRole('button', { name: 'Cancel' }).click();

		expect(workflows.createWebhook).not.toHaveBeenCalled();
		await expect.element(page.getByPlaceholder('e.g. GitHub')).not.toBeInTheDocument();
	});

	it('shows webhook metadata including a named webhook, a deleted channel, and never-triggered state', async () => {
		mockLoad();
		vi.mocked(workflows.listWebhooks).mockResolvedValueOnce([namedWebhook, unnamedWebhook]);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await expect.element(page.getByText('GitHub')).toBeInTheDocument();
		// The unnamed webhook falls back to its id's first 8 characters.
		await expect.element(page.getByText(unnamedWebhook.id.slice(0, 8))).toBeInTheDocument();
		await expect.element(page.getByText('digest-channel', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText('Deleted channel', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByText('never', { exact: false })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Disable' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Enable' })).toBeInTheDocument();
	});

	it('toggles a webhook enabled state, and surfaces an error on failure', async () => {
		mockLoad();
		vi.mocked(workflows.listWebhooks).mockResolvedValueOnce([namedWebhook]);
		vi.mocked(workflows.updateWebhook).mockResolvedValueOnce({ ...namedWebhook, enabled: false });

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Disable' }).click();
		expect(workflows.updateWebhook).toHaveBeenCalledWith('wf-1', 'wh-1', { enabled: false });
		await expect.element(page.getByRole('button', { name: 'Enable' })).toBeInTheDocument();

		vi.mocked(workflows.updateWebhook).mockRejectedValueOnce(new Error('Cannot toggle webhook'));
		await page.getByRole('button', { name: 'Enable' }).click();

		await expect.element(page.getByText('Cannot toggle webhook')).toBeInTheDocument();
	});

	it('rotates a webhook secret and reveals the new value, and surfaces an error on failure', async () => {
		mockLoad();
		vi.mocked(workflows.listWebhooks).mockResolvedValueOnce([namedWebhook]);
		vi.mocked(workflows.rotateWebhookSecret).mockResolvedValueOnce({
			...namedWebhook,
			secret: 'new-secret'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Rotate secret' }).click();

		expect(workflows.rotateWebhookSecret).toHaveBeenCalledWith('wf-1', 'wh-1');
		await expect.element(page.getByLabelText('Secret')).toHaveValue('new-secret');

		vi.mocked(workflows.rotateWebhookSecret).mockRejectedValueOnce(
			new Error('Cannot rotate secret')
		);
		await page.getByRole('button', { name: 'Rotate secret' }).click();

		await expect.element(page.getByText('Cannot rotate secret')).toBeInTheDocument();
	});

	it('removes a webhook, clearing its revealed secret panel, and surfaces an error on failure', async () => {
		mockLoad();
		vi.mocked(workflows.listWebhooks).mockResolvedValueOnce([namedWebhook]);
		vi.mocked(workflows.rotateWebhookSecret).mockResolvedValueOnce({
			...namedWebhook,
			secret: 'shown-secret'
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// Reveal the secret panel first so removal's "clear it if it matches"
		// branch has something to clear.
		await page.getByRole('button', { name: 'Rotate secret' }).click();
		await expect.element(page.getByLabelText('Secret')).toHaveValue('shown-secret');

		// The webhooks section renders above the step chain, so the first
		// "Remove" button on the page belongs to this webhook, not a step.
		vi.mocked(workflows.removeWebhook).mockRejectedValueOnce(new Error('Cannot remove webhook'));
		await page.getByRole('button', { name: 'Remove' }).first().click();
		await expect.element(page.getByText('Cannot remove webhook')).toBeInTheDocument();
		await expect.element(page.getByLabelText('Secret')).toHaveValue('shown-secret');

		vi.mocked(workflows.removeWebhook).mockResolvedValueOnce(undefined);
		await page.getByRole('button', { name: 'Remove' }).first().click();

		expect(workflows.removeWebhook).toHaveBeenCalledWith('wf-1', 'wh-1');
		await expect.element(page.getByLabelText('Secret')).not.toBeInTheDocument();
		await expect.element(page.getByText('GitHub')).not.toBeInTheDocument();
	});

	it('toggles between simple and advanced schedule builder modes', async () => {
		mockLoad();

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		await page.getByRole('button', { name: 'Advanced (cron)' }).click();
		await expect.element(page.getByPlaceholder('0 9 * * *')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Simple' }).click();
		await expect.element(page.getByPlaceholder('0 9 * * *')).not.toBeInTheDocument();
		await expect.element(page.getByLabelText('Frequency')).toBeInTheDocument();
	});

	it('builds a cron expression from the simple hourly picker (#131)', async () => {
		mockLoad();
		const created: WorkflowSchedule = {
			id: 'sched-hourly',
			workflow_id: 'wf-1',
			channel_id: 'ch-1',
			cron_expression: '30 * * * *',
			run_once: false,
			input_content: '',
			enabled: true,
			next_fire_at: '2026-08-09T09:30:00Z',
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
		vi.mocked(workflows.listSchedules).mockResolvedValueOnce([created]);

		await page.getByRole('button', { name: '+ Add schedule' }).click();
		// combobox 2 is Frequency; selecting "hourly" swaps the Time field
		// for a "Minute past the hour" input.
		await page.getByRole('combobox').nth(2).selectOptions('hourly');
		await page.getByLabelText('Minute past the hour').fill('30');
		await expect.element(page.getByText('30 * * * *')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add schedule', exact: true }).click();

		expect(workflows.createSchedule).toHaveBeenCalledWith('wf-1', {
			channel_id: 'ch-1',
			cron_expression: '30 * * * *',
			input_content: ''
		});
	});

	it('inserts a nested workflow step after an existing step in the chain, not just at the top', async () => {
		mockLoad();
		const otherFlow: Workflow = { ...reviewFlow, id: 'wf-2', name: 'other-flow' };
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow, otherFlow]);
		vi.mocked(workflows.createNode).mockResolvedValueOnce({
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Nested mid',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Fetch')).toBeInTheDocument();

		// index 1 is the "+ Add step" between Fetch (n1) and Format (n2).
		await page.getByRole('button', { name: '+ Add step' }).nth(1).click();
		await page.getByPlaceholder('Step name').fill('Nested mid');
		await page.getByRole('combobox').nth(2).selectOptions('workflow');
		await page.getByRole('combobox').nth(3).selectOptions('wf-2');
		await page.getByRole('button', { name: 'Add step', exact: true }).click();

		expect(workflows.createNode).toHaveBeenCalledWith('wf-1', {
			name: 'Nested mid',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});

	it('edits an existing nested-workflow step with the node type locked, keeping the selected child workflow', async () => {
		mockLoad();
		const otherFlow: Workflow = { ...reviewFlow, id: 'wf-2', name: 'other-flow' };
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow, otherFlow]);
		const nestedNode: WorkflowNode = {
			id: 'n3',
			workflow_id: 'wf-1',
			name: 'Nested',
			node_type: 'workflow',
			agent_id: null,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		};
		vi.mocked(workflows.listNodes).mockResolvedValue([fetchNode, formatNode, nestedNode]);
		vi.mocked(workflows.listConnections).mockResolvedValue([
			entryConnection,
			chainConnection,
			{ id: 'c3', workflow_id: 'wf-1', from_node_id: 'n2', to_node_id: 'n3' }
		]);
		vi.mocked(workflows.updateNode).mockResolvedValueOnce(nestedNode);

		render(WorkflowBuilderPage);
		await expect.element(page.getByText('Nested')).toBeInTheDocument();

		// Buttons: header rename Edit (0), Fetch's Edit (1), Format's Edit (2),
		// Nested's Edit (3).
		await page.getByRole('button', { name: 'Edit' }).nth(3).click();

		// lockNodeType renders the type as static text, not a select -- and the
		// workflow picker still needs workflowOptions to list the current pick.
		await expect.element(page.getByText('Workflow', { exact: true })).toBeInTheDocument();
		const workflowPicker = page.getByRole('combobox').last();
		await expect
			.element(workflowPicker.getByRole('option', { name: '/other-flow' }))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Save changes' }).click();

		expect(workflows.updateNode).toHaveBeenCalledWith('wf-1', 'n3', {
			name: 'Nested',
			agent_id: undefined,
			child_workflow_id: 'wf-2',
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		});
	});
});
