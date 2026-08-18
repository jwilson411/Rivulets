// Browser-mode component test for the workflow canvas (06-screens.md →
// Workflow canvas, mockup 1k): full-bleed board with the inspector's
// Step / Triggers / Runs tabs — schedules and webhooks live under
// Triggers, never above the graph.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowBuilderPage from './+page.svelte';
import {
	workflows,
	type Workflow,
	type WorkflowNode,
	type WorkflowRun,
	type WorkflowSchedule,
	type WorkflowWebhookCreated
} from '$lib/api/workflows';
import { agents } from '$lib/api/agents';
import { channels } from '$lib/api/channels';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$app/state', () => ({
	page: { params: { id: 'wf-1' }, url: new URL('http://localhost/workflows/wf-1') }
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

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
		updateConnection: vi.fn(),
		removeConnection: vi.fn(),
		listSchedules: vi.fn(),
		createSchedule: vi.fn(),
		updateSchedule: vi.fn(),
		removeSchedule: vi.fn(),
		previewSchedule: vi.fn(),
		listWebhooks: vi.fn(),
		createWebhook: vi.fn(),
		updateWebhook: vi.fn(),
		rotateWebhookSecret: vi.fn(),
		removeWebhook: vi.fn(),
		listRuns: vi.fn(),
		listNodeRuns: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn(), create: vi.fn() }
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn() }
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const retryCheck: Workflow = {
	id: 'wf-1',
	name: 'retry-check',
	description: 'Checks retry paths',
	published: false,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-02T00:00:00Z'
};

const agentNode: WorkflowNode = {
	id: 'node-1',
	workflow_id: 'wf-1',
	name: 'Agent',
	node_type: 'agent',
	agent_id: 'agent-1',
	child_workflow_id: null,
	config: {},
	retry_max_attempts: 0,
	retry_backoff_seconds: 5,
	position_x: 80,
	position_y: 110
};

const dailySchedule: WorkflowSchedule = {
	id: 'sched-1',
	workflow_id: 'wf-1',
	channel_id: 'chan-1',
	cron_expression: '0 9 * * *',
	run_once: false,
	input_content: 'check retries',
	enabled: true,
	next_fire_at: new Date(Date.now() + 86_400_000).toISOString(),
	last_fired_at: null,
	consecutive_failures: 0,
	name: null,
	created_by: 'human',
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

const createdWebhook: WorkflowWebhookCreated = {
	id: 'hook-1',
	workflow_id: 'wf-1',
	channel_id: 'chan-1',
	name: 'GitHub',
	input_template: null,
	enabled: true,
	last_triggered_at: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z',
	secret: 'whsec-secret-value'
};

const completedRun: WorkflowRun = {
	id: 'run-1',
	workflow_id: 'wf-1',
	rivulet_id: 'riv-1',
	triggered_by: 'slash_command',
	triggered_by_id: null,
	status: 'completed',
	current_node_id: null,
	error_message: null,
	final_output: 'done',
	started_at: new Date().toISOString(),
	completed_at: new Date().toISOString()
};

function seed(overrides?: {
	workflow?: Workflow;
	nodes?: WorkflowNode[];
	schedules?: WorkflowSchedule[];
}) {
	vi.mocked(workflows.get).mockResolvedValue(overrides?.workflow ?? retryCheck);
	vi.mocked(workflows.list).mockResolvedValue([overrides?.workflow ?? retryCheck]);
	vi.mocked(workflows.listNodes).mockResolvedValue(overrides?.nodes ?? []);
	vi.mocked(workflows.listConnections).mockResolvedValue([]);
	vi.mocked(workflows.listSchedules).mockResolvedValue(overrides?.schedules ?? []);
	vi.mocked(workflows.listWebhooks).mockResolvedValue([]);
	vi.mocked(agents.list).mockResolvedValue([
		{
			id: 'agent-1',
			name: 'Writer',
			description: 'Drafts prose',
			instructions: 'Write well',
			model: 'auto',
			fallback_models: [],
			approved_for_unattended_tools: false,
			agentos_agent_id: 'aos-1'
		}
	]);
	vi.mocked(channels.list).mockResolvedValue([
		{
			id: 'chan-1',
			name: 'general',
			description: null,
			team_id: null,
			position: 0,
			archived: false,
			working_directory: null,
			effective_working_directory: null
		}
	]);
}

describe('workflows/[id]/+page.svelte', () => {
	it('shows the /name header with a Draft pill and the palette in plain language', async () => {
		seed();

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('/retry-check', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('Draft', { exact: true })).toBeInTheDocument();
		// Copy-deck step names: "If", never "Conditional"; "Wait for a person".
		await expect.element(page.getByText('If', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('Wait for a person')).toBeInTheDocument();
	});

	it('shows the empty-board hint when there are no steps', async () => {
		seed();

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('Drag a step onto the board.')).toBeInTheDocument();
	});

	it('publishes a draft from the header', async () => {
		seed();
		vi.mocked(workflows.publish).mockResolvedValueOnce({ ...retryCheck, published: true });

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Publish', exact: true }).click();

		expect(workflows.publish).toHaveBeenCalledWith('wf-1');
		await expect.element(page.getByText('Published', { exact: true })).toBeInTheDocument();
	});

	it('renames via a sheet, noting the name is also the slash command', async () => {
		seed();
		vi.mocked(workflows.update).mockResolvedValueOnce({ ...retryCheck, name: 'retry-audit' });

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Rename' }).click();

		await expect.element(page.getByText(/This is also the command:/)).toBeInTheDocument();
		await page.getByLabelText('Name', { exact: true }).fill('retry-audit');
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(workflows.update).toHaveBeenCalledWith('wf-1', {
			name: 'retry-audit',
			description: 'Checks retry paths'
		});
	});

	it('opens a clicked step in the inspector Step tab', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(workflows.updateNode).mockResolvedValueOnce(agentNode);

		render(WorkflowBuilderPage);
		await page.getByTestId('workflow-node-node-1').click();

		await expect.element(page.getByPlaceholder('Step name')).toHaveValue('Agent');
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(workflows.updateNode).toHaveBeenCalledWith(
			'wf-1',
			'node-1',
			expect.objectContaining({ name: 'Agent', agent_id: 'agent-1' })
		);
	});

	it('keeps schedules in the Triggers tab and adds one in plain language', async () => {
		seed();
		vi.mocked(workflows.previewSchedule).mockResolvedValue({
			valid: true,
			next_fire_at: new Date(Date.now() + 3600_000).toISOString(),
			error: null
		});
		vi.mocked(workflows.createSchedule).mockResolvedValueOnce(dailySchedule);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();

		await expect.element(page.getByRole('heading', { name: 'Schedules' })).toBeInTheDocument();
		await page.getByRole('button', { name: 'Add', exact: true }).first().click();
		await page.getByRole('button', { name: 'Add schedule' }).click();

		expect(workflows.createSchedule).toHaveBeenCalledWith('wf-1', {
			channel_id: 'chan-1',
			cron_expression: '0 9 * * *',
			input_content: ''
		});
	});

	it('turns a schedule off from the Triggers tab', async () => {
		seed({ schedules: [dailySchedule] });
		vi.mocked(workflows.updateSchedule).mockResolvedValueOnce({
			...dailySchedule,
			enabled: false
		});

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByRole('button', { name: 'Turn off' }).click();

		expect(workflows.updateSchedule).toHaveBeenCalledWith('wf-1', 'sched-1', { enabled: false });
	});

	it('creates a webhook and reveals its secret exactly once', async () => {
		seed();
		vi.mocked(workflows.createWebhook).mockResolvedValueOnce(createdWebhook);
		vi.mocked(workflows.listWebhooks).mockResolvedValue([createdWebhook]);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByRole('button', { name: 'Add', exact: true }).last().click();
		await page.getByRole('button', { name: 'Add webhook' }).click();

		expect(workflows.createWebhook).toHaveBeenCalledWith('wf-1', {
			channel_id: 'chan-1',
			name: undefined
		});
		await expect
			.element(page.getByText("Save this now — it won't be shown again."))
			.toBeInTheDocument();
		await expect.element(page.getByLabelText('Webhook secret')).toHaveValue('whsec-secret-value');
	});

	it('lists run history under the Runs tab with its steps', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(workflows.listRuns).mockResolvedValue([completedRun]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValue([
			{
				id: 'nr-1',
				node_id: 'node-1',
				attempt: 1,
				status: 'succeeded',
				output_content: 'All good',
				error_message: null,
				started_at: new Date().toISOString(),
				completed_at: new Date().toISOString()
			}
		]);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Runs', exact: true }).click();

		expect(workflows.listRuns).toHaveBeenCalledWith('wf-1');
		await expect.element(page.getByText('completed', { exact: true })).toBeInTheDocument();

		await page.getByText('completed', { exact: true }).click();

		expect(workflows.listNodeRuns).toHaveBeenCalledWith('wf-1', 'run-1');
		await expect.element(page.getByText('All good')).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Show path on board' }))
			.toBeInTheDocument();
	});

	it('hides Publish and read-onlys a published board for guests (#315)', async () => {
		authState.grant = 'invite';
		seed({ workflow: { ...retryCheck, published: true } });

		render(WorkflowBuilderPage);

		await expect.element(page.getByText('/retry-check', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Unpublish' })).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Rename' })).not.toBeInTheDocument();
	});

	it('unpublishes a published workflow from the header', async () => {
		seed({ workflow: { ...retryCheck, published: true } });
		vi.mocked(workflows.unpublish).mockResolvedValueOnce(retryCheck);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Unpublish', exact: true }).click();

		expect(workflows.unpublish).toHaveBeenCalledWith('wf-1');
		await expect.element(page.getByText('Draft', { exact: true })).toBeInTheDocument();
	});

	it('deletes a selected step', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(workflows.removeNode).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await page.getByTestId('workflow-node-node-1').click();
		await page.getByRole('button', { name: 'Delete step' }).click();

		expect(workflows.removeNode).toHaveBeenCalledWith('wf-1', 'node-1');
	});

	it('adds a transform step from a palette drop', async () => {
		seed();
		vi.mocked(workflows.createNode).mockResolvedValueOnce({
			id: 'node-new',
			workflow_id: 'wf-1',
			name: 'Transform',
			node_type: 'transform',
			agent_id: null,
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5,
			position_x: 80,
			position_y: 80
		});

		render(WorkflowBuilderPage);
		const canvas = page.getByTestId('workflow-canvas');
		await expect.element(canvas).toBeInTheDocument();

		const { PALETTE_DRAG_MIME } = await import('$lib/components/WorkflowNodePalette.svelte');
		const dt = new DataTransfer();
		dt.setData(PALETTE_DRAG_MIME, 'transform');
		canvas.element().dispatchEvent(
			new DragEvent('drop', {
				bubbles: true,
				cancelable: true,
				dataTransfer: dt,
				clientX: 200,
				clientY: 160
			})
		);

		await expect.element(page.getByRole('button', { name: 'Add step' })).toBeInTheDocument();
		await page.getByRole('button', { name: 'Add step' }).click();

		expect(workflows.createNode).toHaveBeenCalledWith(
			'wf-1',
			expect.objectContaining({ node_type: 'transform', name: 'Transform' })
		);
	});

	it('removes a schedule from the Triggers tab', async () => {
		seed({ schedules: [dailySchedule] });
		vi.mocked(workflows.removeSchedule).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByRole('button', { name: 'Remove' }).click();

		expect(workflows.removeSchedule).toHaveBeenCalledWith('wf-1', 'sched-1');
	});

	it('paints a run onto the board and clears the overlay', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(workflows.listRuns).mockResolvedValue([completedRun]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValue([
			{
				id: 'nr-1',
				node_id: 'node-1',
				attempt: 1,
				status: 'succeeded',
				output_content: 'All good',
				error_message: null,
				started_at: new Date().toISOString(),
				completed_at: new Date().toISOString()
			}
		]);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Runs', exact: true }).click();
		await page.getByText('completed', { exact: true }).click();
		await page.getByRole('button', { name: 'Show path on board' }).click();

		await expect.element(page.getByTestId('workflow-run-overlay-banner')).toBeInTheDocument();
		await page.getByTestId('workflow-run-overlay-clear').click();
		await expect.element(page.getByTestId('workflow-run-overlay-banner')).not.toBeInTheDocument();
	});

	it('creates an agent inline from a selected agent step', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(agents.create).mockResolvedValueOnce({
			id: 'agent-new',
			name: 'Scout',
			description: 'Looks around',
			instructions: 'Be thorough',
			model: 'auto',
			fallback_models: [],
			approved_for_unattended_tools: false,
			agentos_agent_id: 'aos-new'
		});

		render(WorkflowBuilderPage);
		await page.getByTestId('workflow-node-node-1').click();
		const agentSelect = page.getByRole('combobox');
		await expect.element(agentSelect).toBeInTheDocument();
		const selectEl = agentSelect.element() as HTMLSelectElement;
		selectEl.value = '__create_new_agent__';
		selectEl.dispatchEvent(new Event('change', { bubbles: true }));

		await page.getByLabelText('Agent name').fill('Scout');
		await page.getByLabelText('What this agent does').fill('Looks around');
		await page.getByLabelText('How it should behave').fill('Be thorough');
		await page.getByRole('button', { name: 'Create agent' }).click();

		expect(agents.create).toHaveBeenCalledWith(
			expect.objectContaining({ name: 'Scout', description: 'Looks around' })
		);
	});

	it('turns a webhook off, rotates its secret, and removes it', async () => {
		const listedWebhook = {
			id: 'hook-1',
			workflow_id: 'wf-1',
			channel_id: 'chan-1',
			name: 'GitHub',
			input_template: null,
			enabled: true,
			last_triggered_at: null,
			created_at: '2026-01-01T00:00:00Z',
			updated_at: '2026-01-01T00:00:00Z'
		};
		seed();
		vi.mocked(workflows.listWebhooks).mockResolvedValue([listedWebhook]);
		vi.mocked(workflows.updateWebhook).mockResolvedValueOnce({ ...listedWebhook, enabled: false });
		vi.mocked(workflows.rotateWebhookSecret).mockResolvedValueOnce(createdWebhook);
		vi.mocked(workflows.removeWebhook).mockResolvedValueOnce(undefined);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await expect.element(page.getByText('GitHub')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Turn off' }).click();
		expect(workflows.updateWebhook).toHaveBeenCalledWith('wf-1', 'hook-1', { enabled: false });

		await page.getByRole('button', { name: 'New secret' }).click();
		expect(workflows.rotateWebhookSecret).toHaveBeenCalledWith('wf-1', 'hook-1');
		await expect.element(page.getByLabelText('Webhook secret')).toHaveValue('whsec-secret-value');

		await page.getByRole('button', { name: 'Remove' }).click();
		expect(workflows.removeWebhook).toHaveBeenCalledWith('wf-1', 'hook-1');
	});

	it('saves If a run fails remediations from the Triggers tab', async () => {
		const other: Workflow = { ...retryCheck, id: 'wf-2', name: 'notify-on-call' };
		seed();
		vi.mocked(workflows.list).mockResolvedValue([retryCheck, other]);
		vi.mocked(workflows.update).mockResolvedValueOnce({
			...retryCheck,
			on_failure_workflow_id: 'wf-2'
		});

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page
			.getByLabelText('Run another workflow with the failure as its input')
			.selectOptions('wf-2');

		expect(workflows.update).toHaveBeenCalledWith('wf-1', { on_failure_workflow_id: 'wf-2' });
	});

	it('saves the on-call agent from the Triggers tab', async () => {
		seed();
		vi.mocked(workflows.update).mockResolvedValueOnce({
			...retryCheck,
			on_call_agent_id: 'agent-1'
		});

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByLabelText('Bring in an agent').selectOptions('agent-1');

		expect(workflows.update).toHaveBeenCalledWith('wf-1', { on_call_agent_id: 'agent-1' });
	});

	it('shows the empty Runs tab with the slash-command hint', async () => {
		seed();
		vi.mocked(workflows.listRuns).mockResolvedValue([]);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Runs', exact: true }).click();

		await expect.element(page.getByText(/Nothing has run yet/)).toBeInTheDocument();
		await expect.element(page.getByRole('code')).toHaveTextContent('/retry-check');
	});

	it('builds a weekly cron from the plain-language schedule form', async () => {
		seed();
		vi.mocked(workflows.previewSchedule).mockResolvedValue({
			valid: true,
			next_fire_at: new Date(Date.now() + 3600_000).toISOString(),
			error: null
		});
		vi.mocked(workflows.createSchedule).mockResolvedValueOnce(dailySchedule);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByRole('button', { name: 'Add', exact: true }).first().click();
		await page.getByLabelText('How often').selectOptions('weekly');
		await page.getByRole('button', { name: 'Add schedule' }).click();

		expect(workflows.createSchedule).toHaveBeenCalledWith(
			'wf-1',
			expect.objectContaining({
				channel_id: 'chan-1',
				cron_expression: expect.stringMatching(/^\d+ \d+ \* \* 1$/)
			})
		);
	});

	it('turns a disabled schedule back on', async () => {
		seed({ schedules: [{ ...dailySchedule, enabled: false }] });
		vi.mocked(workflows.updateSchedule).mockResolvedValueOnce(dailySchedule);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();
		await page.getByRole('button', { name: 'Turn on' }).click();

		expect(workflows.updateSchedule).toHaveBeenCalledWith('wf-1', 'sched-1', { enabled: true });
	});

	it('explains a pending agent-created schedule', async () => {
		seed({
			schedules: [
				{
					...dailySchedule,
					created_by: 'agent-1',
					enabled: false,
					last_fired_at: null
				}
			]
		});

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Triggers' }).click();

		await expect
			.element(page.getByText('Created by an agent — waiting on your approval.'))
			.toBeInTheDocument();
	});

	it('shows a failed run error and its step error', async () => {
		seed({ nodes: [agentNode] });
		vi.mocked(workflows.listRuns).mockResolvedValue([
			{
				...completedRun,
				id: 'run-fail',
				status: 'failed',
				error_message: 'Conditional stopped the branch'
			}
		]);
		vi.mocked(workflows.listNodeRuns).mockResolvedValue([
			{
				id: 'nr-fail',
				node_id: 'node-1',
				attempt: 1,
				status: 'failed',
				output_content: null,
				error_message: 'no match',
				started_at: new Date().toISOString(),
				completed_at: new Date().toISOString()
			}
		]);

		render(WorkflowBuilderPage);
		await page.getByRole('button', { name: 'Runs', exact: true }).click();
		await page.getByText('failed', { exact: true }).click();

		await expect.element(page.getByText('Conditional stopped the branch')).toBeInTheDocument();
		await expect.element(page.getByText('no match')).toBeInTheDocument();
	});

	it('shows a plain-language error when the workflow fails to load', async () => {
		vi.mocked(workflows.get).mockRejectedValue(new Error('boom'));
		vi.mocked(workflows.list).mockResolvedValue([]);
		vi.mocked(workflows.listNodes).mockResolvedValue([]);
		vi.mocked(workflows.listConnections).mockResolvedValue([]);
		vi.mocked(workflows.listSchedules).mockResolvedValue([]);
		vi.mocked(workflows.listWebhooks).mockResolvedValue([]);
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(channels.list).mockResolvedValue([]);

		render(WorkflowBuilderPage);

		await expect.element(page.getByText("Couldn't load this workflow.")).toBeInTheDocument();
	});
});
