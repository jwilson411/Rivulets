// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/workflows and $app/paths' resolve() for the
// per-workflow and per-rivulet links, so both are mocked.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowsPage from './+page.svelte';
import { workflows, type FailedWorkflowRun, type Workflow } from '$lib/api/workflows';

vi.mock('$lib/api/workflows', () => ({
	workflows: {
		list: vi.fn(),
		create: vi.fn(),
		remove: vi.fn(),
		listFailedRuns: vi.fn()
	}
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		if (!params) return path;
		let resolved = path;
		for (const [key, value] of Object.entries(params)) {
			resolved = resolved.replace(`[${key}]`, value);
		}
		return resolved;
	}
}));

const reviewFlow: Workflow = {
	id: 'wf-1',
	name: 'review-pr',
	description: 'Runs a PR through security review then docs',
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: '2026-08-01T00:00:00Z',
	updated_at: '2026-08-01T00:00:00Z'
};

const doomedRun: FailedWorkflowRun = {
	id: 'run-1',
	workflow_id: 'wf-2',
	workflow_name: 'doomed-flow',
	rivulet_id: 'riv-1',
	channel_id: 'chan-1',
	triggered_by: 'schedule',
	triggered_by_id: 'sched-1',
	status: 'failed',
	current_node_id: null,
	error_message: 'provider is down',
	final_output: null,
	started_at: '2026-08-09T06:00:00Z',
	completed_at: '2026-08-09T06:00:05Z'
};

beforeEach(() => {
	// #94: every test renders the failed-runs panel on mount -- default it
	// to empty so existing scenarios that don't care about it don't have
	// to stub a resolved value themselves.
	vi.mocked(workflows.listFailedRuns).mockResolvedValue([]);
});

afterEach(() => {
	vi.clearAllMocks();
});

describe('workflows/+page.svelte', () => {
	it('lists existing workflows with a link into the builder', async () => {
		vi.mocked(workflows.list).mockResolvedValue([reviewFlow]);

		render(WorkflowsPage);

		await expect.element(page.getByText('review-pr')).toBeInTheDocument();
		await expect
			.element(page.getByRole('link', { name: /review-pr/ }))
			.toHaveAttribute('href', '/workflows/wf-1');
	});

	it('shows an empty state when there are no workflows', async () => {
		vi.mocked(workflows.list).mockResolvedValue([]);

		render(WorkflowsPage);

		await expect.element(page.getByText(/No workflows yet/)).toBeInTheDocument();
	});

	it('creates a workflow via the new-workflow form and refreshes the list', async () => {
		vi.mocked(workflows.list).mockResolvedValueOnce([]).mockResolvedValueOnce([reviewFlow]);
		vi.mocked(workflows.create).mockResolvedValueOnce(reviewFlow);

		render(WorkflowsPage);
		await expect.element(page.getByText(/No workflows yet/)).toBeInTheDocument();

		await page.getByPlaceholder('my-workflow').fill('review-pr');
		await page
			.getByPlaceholder('Description (optional)')
			.fill('Runs a PR through security review then docs');
		await page.getByRole('button', { name: 'Create workflow' }).click();

		expect(workflows.create).toHaveBeenCalledWith({
			name: 'review-pr',
			description: 'Runs a PR through security review then docs'
		});
		await expect.element(page.getByText('review-pr')).toBeInTheDocument();
	});

	it('surfaces a server-rejected create instead of failing silently', async () => {
		vi.mocked(workflows.list).mockResolvedValue([]);
		vi.mocked(workflows.create).mockRejectedValueOnce(
			new Error("A workflow named 'review-pr' already exists")
		);

		render(WorkflowsPage);
		await page.getByPlaceholder('my-workflow').fill('review-pr');
		await page.getByRole('button', { name: 'Create workflow' }).click();

		await expect
			.element(page.getByText("A workflow named 'review-pr' already exists"))
			.toBeInTheDocument();
	});

	it('deletes a workflow and refreshes the list', async () => {
		vi.mocked(workflows.list).mockResolvedValueOnce([reviewFlow]).mockResolvedValueOnce([]);
		vi.mocked(workflows.remove).mockResolvedValueOnce(undefined);

		render(WorkflowsPage);
		await expect.element(page.getByText('review-pr')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		expect(workflows.remove).toHaveBeenCalledWith('wf-1');
		await expect.element(page.getByText(/No workflows yet/)).toBeInTheDocument();
	});

	it('shows no failed-runs panel when nothing has failed', async () => {
		vi.mocked(workflows.list).mockResolvedValue([]);

		render(WorkflowsPage);
		await expect.element(page.getByText(/No workflows yet/)).toBeInTheDocument();

		await expect.element(page.getByText(/Failed runs/)).not.toBeInTheDocument();
	});

	it('surfaces failed runs across workflows with a link to the rivulet', async () => {
		vi.mocked(workflows.list).mockResolvedValue([]);
		vi.mocked(workflows.listFailedRuns).mockResolvedValue([doomedRun]);

		render(WorkflowsPage);

		await expect.element(page.getByText('Failed runs (1)')).toBeInTheDocument();
		await expect.element(page.getByText('provider is down')).toBeInTheDocument();
		await expect
			.element(page.getByRole('link', { name: '/doomed-flow' }))
			.toHaveAttribute('href', '/workflows/wf-2');
		await expect
			.element(page.getByRole('link', { name: 'View rivulet' }))
			.toHaveAttribute('href', '/channels/chan-1/rivulets/riv-1');
	});
});
