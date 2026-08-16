// Browser-mode component test for Evals (06-screens.md → Evals, mockup
// 2f): suite cards with a big Run button and a pass/fail pill; cases open
// in a sheet; judge types in plain language.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import EvalsPage from './+page.svelte';
import { agents, type Agent } from '$lib/api/agents';
import { workflows, type Workflow } from '$lib/api/workflows';
import { evals, type EvalCase, type EvalRun, type EvalSuite } from '$lib/api/evals';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/evals', () => ({
	evals: {
		listSuites: vi.fn(),
		createSuite: vi.fn(),
		deleteSuite: vi.fn(),
		listCases: vi.fn(),
		createCase: vi.fn(),
		deleteCase: vi.fn(),
		run: vi.fn(),
		listRuns: vi.fn(),
		listResults: vi.fn()
	}
}));

vi.mock('$lib/api/agents', () => ({
	agents: { list: vi.fn(), getToolScopes: vi.fn() }
}));

vi.mock('$lib/api/workflows', () => ({
	workflows: { list: vi.fn(), listNodes: vi.fn() }
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

const assistant: Agent = {
	id: 'agent-1',
	name: 'Assistant',
	description: 'Generalist',
	instructions: 'Help',
	model: 'auto',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'aos-1'
};

const retryCheck: Workflow = {
	id: 'wf-1',
	name: 'retry-check',
	description: null,
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

const retryCoverage: EvalSuite = {
	id: 'suite-1',
	name: 'retry-coverage',
	description: null,
	subject_type: 'workflow',
	subject_name: 'retry-check',
	agent_id: null,
	workflow_id: 'wf-1',
	case_count: 3,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

const lastRun: EvalRun = {
	id: 'run-1',
	suite_id: 'suite-1',
	status: 'completed',
	triggered_by: 'human',
	triggered_by_id: null,
	case_count: 3,
	pass_count: 2,
	fail_count: 1,
	error_count: 0,
	started_at: new Date().toISOString(),
	completed_at: new Date().toISOString()
};

const exactCase: EvalCase = {
	id: 'case-1',
	suite_id: 'suite-1',
	name: 'Transient failure, recovers',
	input_content: 'simulate a transient failure',
	judge_type: 'llm_judge',
	expected_output: null,
	rubric: 'Recovers and says so',
	expected_tool_name: null,
	expected_tool_args: null
};

function seed(overrides?: { suites?: EvalSuite[]; runs?: EvalRun[] }) {
	vi.mocked(evals.listSuites).mockResolvedValue(overrides?.suites ?? [retryCoverage]);
	vi.mocked(agents.list).mockResolvedValue([assistant]);
	vi.mocked(workflows.list).mockResolvedValue([retryCheck]);
	vi.mocked(evals.listRuns).mockResolvedValue(overrides?.runs ?? [lastRun]);
	vi.mocked(evals.listCases).mockResolvedValue([exactCase]);
}

describe('evals/+page.svelte', () => {
	it('shows suite cards with target, case count, and the last run pill', async () => {
		seed();

		render(EvalsPage);

		await expect.element(page.getByText('retry-coverage')).toBeInTheDocument();
		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();
		await expect.element(page.getByText('3 cases')).toBeInTheDocument();
		await expect.element(page.getByText('2/3 passed')).toBeInTheDocument();
	});

	it('runs a suite from the big Run button', async () => {
		seed();
		vi.mocked(evals.run).mockResolvedValueOnce(lastRun);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run', exact: true }).click();

		expect(evals.run).toHaveBeenCalledWith('suite-1');
	});

	it('opens cases in a sheet with plain-language judge labels', async () => {
		seed();

		render(EvalsPage);
		await page.getByRole('button', { name: 'Cases' }).click();

		await expect.element(page.getByText('Transient failure, recovers')).toBeInTheDocument();
		await expect.element(page.getByText('A model grades it')).toBeInTheDocument();
	});

	it('adds a case from the sheet', async () => {
		seed();
		vi.mocked(evals.createCase).mockResolvedValueOnce(exactCase);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Cases' }).click();
		await page.getByRole('button', { name: 'Add case' }).click();

		await page.getByLabelText('Case name').fill('Exhausts retries');
		await page.getByLabelText('Input').fill('always fail');
		await page.getByLabelText("How it's judged").selectOptions('substring');
		await page.getByLabelText('Expected reply').fill('gave up');
		await page.getByRole('button', { name: 'Add case' }).last().click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'Exhausts retries',
			input_content: 'always fail',
			judge_type: 'substring',
			expected_output: 'gave up',
			rubric: undefined,
			expected_tool_name: undefined,
			expected_tool_args: undefined
		});
	});

	it('creates a suite targeting a workflow from the New suite sheet', async () => {
		seed({ suites: [] });
		vi.mocked(evals.createSuite).mockResolvedValueOnce(retryCoverage);

		render(EvalsPage);
		await page.getByRole('button', { name: 'New suite' }).click();

		await page.getByLabelText('Name').fill('retry-coverage');
		await page.getByRole('button', { name: 'A workflow' }).click();
		await page.getByLabelText('Target').selectOptions('wf-1');
		await page.getByRole('button', { name: 'Create suite' }).click();

		expect(evals.createSuite).toHaveBeenCalledWith({
			name: 'retry-coverage',
			description: undefined,
			agent_id: undefined,
			workflow_id: 'wf-1'
		});
	});

	it('shows run history with per-case results', async () => {
		seed();
		vi.mocked(evals.listResults).mockResolvedValue([
			{
				id: 'res-1',
				run_id: 'run-1',
				case_id: 'case-1',
				status: 'passed',
				score: 0.9,
				actual_output: 'Recovered fine',
				actual_tool_calls: null,
				judge_reasoning: 'Matches the rubric',
				error_message: null,
				started_at: new Date().toISOString(),
				completed_at: new Date().toISOString()
			}
		]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'History' }).click();
		await page
			.getByText(/passed/)
			.last()
			.click();

		expect(evals.listResults).toHaveBeenCalledWith('suite-1', 'run-1');
		await expect.element(page.getByText('Recovered fine')).toBeInTheDocument();
	});

	it('deletes a suite behind a confirm sheet', async () => {
		seed();
		vi.mocked(evals.deleteSuite).mockResolvedValueOnce(undefined);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Cases' }).click();
		await page.getByRole('button', { name: 'Delete suite' }).click();

		expect(evals.deleteSuite).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Delete suite' }).click();

		expect(evals.deleteSuite).toHaveBeenCalledWith('suite-1');
	});

	it('never offers a guest a draft workflow it cannot run (#355)', async () => {
		authState.grant = 'invite';
		seed({ suites: [] });
		vi.mocked(workflows.list).mockResolvedValue([{ ...retryCheck, published: false }]);
		vi.mocked(agents.getToolScopes).mockResolvedValue({ scopes: [] });
		vi.mocked(workflows.listNodes).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'New suite' }).click();
		await page.getByRole('button', { name: 'A workflow' }).click();

		await expect.element(page.getByLabelText('Target')).not.toContainHTML('retry-check');
	});

	it('shows a quiet error with retry when suites fail to load', async () => {
		vi.mocked(evals.listSuites).mockRejectedValue(new Error('boom'));
		vi.mocked(agents.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);

		render(EvalsPage);

		await expect.element(page.getByText("Couldn't load evals.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
