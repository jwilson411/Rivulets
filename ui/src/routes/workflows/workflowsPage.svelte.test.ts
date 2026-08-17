// Browser-mode component test for the Workflows list (06-screens.md →
// Workflows list, mockup 2e): /name is the title, a failed-run banner with
// "Open conversation", All/Published/Draft chips, and a name sheet that
// leads straight to the canvas.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkflowsPage from './+page.svelte';
import { workflows, type FailedWorkflowRun, type Workflow } from '$lib/api/workflows';
import { goto } from '$app/navigation';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

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
	workflows: { list: vi.fn(), listFailedRuns: vi.fn(), create: vi.fn(), remove: vi.fn() }
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
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-02T00:00:00Z'
};

const nightlyDigest: Workflow = {
	...retryCheck,
	id: 'wf-2',
	name: 'nightly-digest',
	published: false
};

const failedRun: FailedWorkflowRun = {
	id: 'run-1',
	workflow_id: 'wf-1',
	workflow_name: 'retry-check',
	rivulet_id: 'riv-1',
	channel_id: 'chan-1',
	triggered_by: 'slash_command',
	triggered_by_id: null,
	status: 'failed',
	current_node_id: null,
	error_message: 'Conditional stopped the branch',
	final_output: null,
	started_at: new Date().toISOString(),
	completed_at: null
};

function seed(list: Workflow[] = [retryCheck, nightlyDigest], failed: FailedWorkflowRun[] = []) {
	vi.mocked(workflows.list).mockResolvedValue(list);
	vi.mocked(workflows.listFailedRuns).mockResolvedValue(failed);
}

describe('workflows/+page.svelte', () => {
	it('lists workflows as /name with a Published or Draft pill', async () => {
		seed();

		render(WorkflowsPage);

		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();
		await expect.element(page.getByText('Published', { exact: true }).first()).toBeInTheDocument();
		await expect.element(page.getByText('/nightly-digest')).toBeInTheDocument();
	});

	it('filters by the Draft chip', async () => {
		seed();

		render(WorkflowsPage);
		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Draft', exact: true }).click();

		await expect.element(page.getByText('/retry-check')).not.toBeInTheDocument();
		await expect.element(page.getByText('/nightly-digest')).toBeInTheDocument();
	});

	it('shows the failed-run banner with a way into the conversation (#94)', async () => {
		seed([retryCheck], [failedRun]);

		render(WorkflowsPage);

		await expect.element(page.getByText('Failed runs')).toBeInTheDocument();
		await expect
			.element(page.getByText('Conditional stopped the branch', { exact: false }))
			.toBeInTheDocument();
		const link = page.getByRole('link', { name: 'Open conversation' });
		await expect.element(link).toHaveAttribute('href', '/channels/chan-1/rivulets/riv-1');
	});

	it('creates a workflow from the name sheet and opens its canvas', async () => {
		seed();
		vi.mocked(workflows.create).mockResolvedValueOnce({ ...nightlyDigest, id: 'wf-3' });

		render(WorkflowsPage);
		await page.getByRole('button', { name: 'New workflow' }).click();

		await expect
			.element(page.getByText('This is also the command: /retry-check'))
			.toBeInTheDocument();
		await page.getByLabelText('Name').fill('daily-brief');
		await page.getByRole('button', { name: 'Create workflow' }).click();

		expect(workflows.create).toHaveBeenCalledWith({ name: 'daily-brief', description: null });
		expect(goto).toHaveBeenCalledWith('/workflows/wf-3');
	});

	it('deletes a workflow behind a confirm sheet (owner only)', async () => {
		seed([retryCheck]);
		vi.mocked(workflows.remove).mockResolvedValueOnce(undefined);

		render(WorkflowsPage);
		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete', exact: true }).click();
		expect(workflows.remove).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Delete workflow' }).click();

		expect(workflows.remove).toHaveBeenCalledWith('wf-1');
	});

	it('hides the delete affordance from guests', async () => {
		authState.grant = 'invite';
		seed([retryCheck]);

		render(WorkflowsPage);
		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();

		await expect
			.element(page.getByRole('button', { name: 'Delete', exact: true }))
			.not.toBeInTheDocument();
	});

	it('shows a quiet error with retry when workflows fail to load', async () => {
		vi.mocked(workflows.list).mockRejectedValue(new Error('boom'));
		vi.mocked(workflows.listFailedRuns).mockResolvedValue([]);

		render(WorkflowsPage);

		await expect.element(page.getByText("Couldn't load workflows.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
