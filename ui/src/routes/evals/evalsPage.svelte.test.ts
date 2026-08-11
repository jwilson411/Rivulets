// Browser-mode component test (see agentsPage.svelte.test.ts). This route
// depends on $lib/api/evals, $lib/api/agents, and $lib/api/workflows, all
// mocked here -- no SvelteKit routing modules are involved.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import EvalsPage from './+page.svelte';
import {
	evals,
	type EvalCase,
	type EvalCaseResult,
	type EvalRun,
	type EvalSuite
} from '$lib/api/evals';
import { agents, type Agent } from '$lib/api/agents';
import { workflows, type Workflow } from '$lib/api/workflows';

vi.mock('$lib/api/evals', () => ({
	evals: {
		listSuites: vi.fn(),
		createSuite: vi.fn(),
		updateSuite: vi.fn(),
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
	agents: { list: vi.fn() }
}));

vi.mock('$lib/api/workflows', () => ({
	workflows: { list: vi.fn() }
}));

const researcher: Agent = {
	id: 'agent-1',
	name: 'Researcher',
	description: 'Looks things up',
	instructions: 'Be thorough',
	model: 'anthropic:claude-haiku-4-5-20251001',
	fallback_models: [],
	approved_for_unattended_tools: false,
	agentos_agent_id: 'agentos-1'
};

const digestWorkflow: Workflow = {
	id: 'wf-1',
	name: 'digest',
	description: null,
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

const agentSuite: EvalSuite = {
	id: 'suite-1',
	name: 'greeting-suite',
	description: null,
	agent_id: 'agent-1',
	workflow_id: null,
	subject_type: 'agent',
	subject_name: 'Researcher',
	case_count: 1,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: '2026-01-01T00:00:00Z'
};

const exactCase: EvalCase = {
	id: 'case-1',
	suite_id: 'suite-1',
	name: 'greets',
	input_content: 'hi',
	judge_type: 'exact',
	expected_output: 'hello',
	rubric: null,
	expected_tool_name: null,
	expected_tool_args: null
};

afterEach(() => {
	vi.clearAllMocks();
});

function stubLists() {
	vi.mocked(agents.list).mockResolvedValue([researcher]);
	vi.mocked(workflows.list).mockResolvedValue([digestWorkflow]);
}

describe('evals/+page.svelte', () => {
	it('lists suites spanning both subject types', async () => {
		stubLists();
		const workflowSuite: EvalSuite = {
			...agentSuite,
			id: 'suite-2',
			name: 'digest-suite',
			agent_id: null,
			workflow_id: 'wf-1',
			subject_type: 'workflow',
			subject_name: 'digest',
			case_count: 0
		};
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite, workflowSuite]);

		render(EvalsPage);

		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();
		await expect.element(page.getByText('digest-suite')).toBeInTheDocument();
	});

	it('shows an error when suites fail to load', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockRejectedValueOnce(new Error('Failed to load eval suites'));

		render(EvalsPage);

		await expect.element(page.getByText('Failed to load eval suites')).toBeInTheDocument();
	});

	it('shows an empty state when there are no suites', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);

		await expect
			.element(page.getByText('No eval suites yet — create one to start catching regressions.'))
			.toBeInTheDocument();
	});

	it('creates an agent-attached suite via the New suite form', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValueOnce([]).mockResolvedValueOnce([agentSuite]);
		vi.mocked(evals.createSuite).mockResolvedValueOnce(agentSuite);

		render(EvalsPage);
		await expect
			.element(page.getByText('No eval suites yet — create one to start catching regressions.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByPlaceholder('greeting-regressions').fill('greeting-suite');
		await page.getByRole('combobox').selectOptions('agent-1');
		await page.getByRole('button', { name: 'Create suite' }).click();

		expect(evals.createSuite).toHaveBeenCalledWith({
			name: 'greeting-suite',
			description: undefined,
			agent_id: 'agent-1',
			workflow_id: undefined
		});
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();
	});

	it('shows an error when suite creation fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([]);
		vi.mocked(evals.createSuite).mockRejectedValueOnce(new Error('Failed to create eval suite'));

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByPlaceholder('greeting-regressions').fill('greeting-suite');
		await page.getByRole('combobox').selectOptions('agent-1');
		await page.getByRole('button', { name: 'Create suite' }).click();

		await expect.element(page.getByText('Failed to create eval suite')).toBeInTheDocument();
	});

	it('omits the structural judge option for a workflow-attached suite', async () => {
		stubLists();
		const workflowSuite: EvalSuite = {
			...agentSuite,
			id: 'suite-2',
			name: 'digest-suite',
			agent_id: null,
			workflow_id: 'wf-1',
			subject_type: 'workflow',
			subject_name: 'digest',
			case_count: 0
		};
		vi.mocked(evals.listSuites).mockResolvedValue([workflowSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);

		render(EvalsPage);
		await expect.element(page.getByText('digest-suite')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		const judgeSelect = page.getByRole('combobox').first();
		const options = judgeSelect.element().querySelectorAll('option');
		const values = Array.from(options).map((o) => (o as HTMLOptionElement).value);
		expect(values).not.toContain('structural');
	});

	it('deletes a suite via evals.deleteSuite and refreshes the list', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValueOnce([agentSuite]).mockResolvedValueOnce([]);
		vi.mocked(evals.deleteSuite).mockResolvedValueOnce(undefined);

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		expect(evals.deleteSuite).toHaveBeenCalledWith('suite-1');
		await expect
			.element(page.getByText('No eval suites yet — create one to start catching regressions.'))
			.toBeInTheDocument();
	});

	it('adds a case to a suite and lists it', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([]).mockResolvedValueOnce([exactCase]);
		vi.mocked(evals.createCase).mockResolvedValueOnce(exactCase);

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		const nameInputs = page.getByRole('textbox');
		await nameInputs.first().fill('greets');
		// The "Input" textarea is the second text control in the add-case form.
		await page.getByRole('textbox').nth(1).fill('hi');
		await page.getByRole('textbox').nth(2).fill('hello'); // Expected output (judge_type defaults to 'exact')

		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'greets',
			input_content: 'hi',
			judge_type: 'exact',
			expected_output: 'hello',
			rubric: undefined,
			expected_tool_name: undefined,
			expected_tool_args: undefined
		});
		await expect.element(page.getByText('greets')).toBeInTheDocument();
	});

	it('deletes a case via evals.deleteCase', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([exactCase]).mockResolvedValueOnce([]);
		vi.mocked(evals.deleteCase).mockResolvedValueOnce(undefined);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).click();

		expect(evals.deleteCase).toHaveBeenCalledWith('suite-1', 'case-1');
	});

	it('runs a suite via evals.run and shows the resulting summary', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: 'human-1',
			case_count: 1,
			pass_count: 1,
			fail_count: 0,
			error_count: 0,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.run).mockResolvedValueOnce(run);
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Run', exact: true }).click();

		expect(evals.run).toHaveBeenCalledWith('suite-1');
		await expect.element(page.getByText('1/1 passed')).toBeInTheDocument();
	});

	it('expands a run to show per-case results', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: null,
			case_count: 1,
			pass_count: 0,
			fail_count: 1,
			error_count: 0,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);
		const result: EvalCaseResult = {
			id: 'result-1',
			run_id: 'run-1',
			case_id: 'case-1',
			status: 'failed',
			score: null,
			actual_output: 'goodbye',
			actual_tool_calls: null,
			judge_reasoning: null,
			error_message: null,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listResults).mockResolvedValueOnce([result]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Run history' }).click();
		await expect.element(page.getByText('0/1 passed')).toBeInTheDocument();

		await page.getByText('0/1 passed').click();

		expect(evals.listResults).toHaveBeenCalledWith('suite-1', 'run-1');
		await expect.element(page.getByText('goodbye')).toBeInTheDocument();
	});

	it('shows an error when suite deletion fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.deleteSuite).mockRejectedValueOnce(new Error('Failed to delete eval suite'));

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Failed to delete eval suite')).toBeInTheDocument();
	});

	it('collapses the cases panel on a second click', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Hide cases' }).click();
		await expect.element(page.getByText('greets')).not.toBeInTheDocument();
	});

	it('shows an error when cases fail to load', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockRejectedValueOnce(new Error('Failed to load cases'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();

		await expect.element(page.getByText('Failed to load cases')).toBeInTheDocument();
	});

	it('rejects invalid JSON in expected tool args for a structural case', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		await page.getByRole('textbox').first().fill('calls search');
		await page.getByRole('textbox').nth(1).fill('find cats');
		await page.getByLabelText('Judge').selectOptions('structural');
		await page.getByPlaceholder('search').fill('search_web');
		await page.getByPlaceholder('{"query": "cats"}').fill('not json');
		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).not.toHaveBeenCalled();
		await expect
			.element(page.getByText('Expected tool args must be valid JSON (or left blank).'))
			.toBeInTheDocument();
	});

	it('shows an error when adding a case fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);
		vi.mocked(evals.createCase).mockRejectedValueOnce(new Error('Failed to add case'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();
		await page.getByRole('textbox').first().fill('greets');
		await page.getByRole('textbox').nth(1).fill('hi');

		await page.getByRole('button', { name: 'Add case' }).click();

		await expect.element(page.getByText('Failed to add case')).toBeInTheDocument();
	});

	it('adds an llm_judge case with a rubric', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([]).mockResolvedValueOnce([]);
		vi.mocked(evals.createCase).mockResolvedValueOnce({
			...exactCase,
			judge_type: 'llm_judge',
			expected_output: null,
			rubric: 'Should acknowledge the request.'
		});

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();
		await page.getByRole('textbox').first().fill('greets');
		await page.getByRole('textbox').nth(1).fill('hi');
		await page.getByLabelText('Judge').selectOptions('llm_judge');
		await page.getByRole('textbox').nth(2).fill('Should acknowledge the request.');

		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'greets',
			input_content: 'hi',
			judge_type: 'llm_judge',
			expected_output: undefined,
			rubric: 'Should acknowledge the request.',
			expected_tool_name: undefined,
			expected_tool_args: undefined
		});
	});

	it('adds a structural case with valid tool args JSON', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([]).mockResolvedValueOnce([]);
		vi.mocked(evals.createCase).mockResolvedValueOnce({
			...exactCase,
			judge_type: 'structural',
			expected_output: null,
			expected_tool_name: 'search_web'
		});

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();
		await page.getByRole('textbox').first().fill('calls search');
		await page.getByRole('textbox').nth(1).fill('find cats');
		await page.getByLabelText('Judge').selectOptions('structural');
		await page.getByPlaceholder('search').fill('search_web');
		await page.getByPlaceholder('{"query": "cats"}').fill('{"query": "cats"}');

		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'calls search',
			input_content: 'find cats',
			judge_type: 'structural',
			expected_output: undefined,
			rubric: undefined,
			expected_tool_name: 'search_web',
			expected_tool_args: { query: 'cats' }
		});
	});

	it('cancels adding a case without calling evals.createCase', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();
		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect.element(page.getByRole('button', { name: '+ Add case' })).toBeInTheDocument();
		expect(evals.createCase).not.toHaveBeenCalled();
	});

	it('shows an error when deleting a case fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);
		vi.mocked(evals.deleteCase).mockRejectedValueOnce(new Error('Failed to delete case'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Remove' }).click();

		await expect.element(page.getByText('Failed to delete case')).toBeInTheDocument();
	});

	it('shows an error when running a suite fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.run).mockRejectedValueOnce(new Error('Failed to run eval suite'));

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Run', exact: true }).click();

		await expect.element(page.getByText('Failed to run eval suite')).toBeInTheDocument();
	});

	it('collapses run history on a second click', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listRuns).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run history' }).click();
		await expect
			.element(page.getByText('No runs yet — click "Run" to try this suite.'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Hide runs' }).click();
		await expect
			.element(page.getByText('No runs yet — click "Run" to try this suite.'))
			.not.toBeInTheDocument();
	});

	it('shows an error when run history fails to load', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listRuns).mockRejectedValueOnce(new Error('Failed to load run history'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run history' }).click();

		await expect.element(page.getByText('Failed to load run history')).toBeInTheDocument();
	});

	it('collapses expanded run results on a second click, then re-expands from cache', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: null,
			case_count: 1,
			pass_count: 1,
			fail_count: 0,
			error_count: 0,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);
		vi.mocked(evals.listResults).mockResolvedValueOnce([
			{
				id: 'result-1',
				run_id: 'run-1',
				case_id: 'case-1',
				status: 'passed',
				score: null,
				actual_output: 'hello',
				actual_tool_calls: null,
				judge_reasoning: null,
				error_message: null,
				started_at: '2026-01-01T00:00:00Z',
				completed_at: '2026-01-01T00:00:01Z'
			}
		]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run history' }).click();
		await expect.element(page.getByText('1/1 passed')).toBeInTheDocument();

		await page.getByText('1/1 passed').click();
		await expect.element(page.getByText('hello')).toBeInTheDocument();

		await page.getByText('1/1 passed').click();
		await expect.element(page.getByText('hello')).not.toBeInTheDocument();

		await page.getByText('1/1 passed').click();
		await expect.element(page.getByText('hello')).toBeInTheDocument();
		expect(evals.listResults).toHaveBeenCalledTimes(1);
	});

	it('shows an error when run results fail to load', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: null,
			case_count: 1,
			pass_count: 1,
			fail_count: 0,
			error_count: 0,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);
		vi.mocked(evals.listResults).mockRejectedValueOnce(new Error('Failed to load results'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run history' }).click();
		await page.getByText('1/1 passed').click();

		await expect.element(page.getByText('Failed to load results')).toBeInTheDocument();
	});

	it('shows a case result with a score, error status, an amber run summary, and reasoning/error text', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: null,
			case_count: 2,
			pass_count: 0,
			fail_count: 0,
			error_count: 2,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);
		vi.mocked(evals.listResults).mockResolvedValueOnce([
			{
				id: 'result-1',
				run_id: 'run-1',
				case_id: 'case-1',
				status: 'error',
				score: 0.5,
				actual_output: null,
				actual_tool_calls: null,
				judge_reasoning: 'The reply drifted off-topic.',
				error_message: 'Provider timed out',
				started_at: '2026-01-01T00:00:00Z',
				completed_at: '2026-01-01T00:00:01Z'
			}
		]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run history' }).click();
		await expect.element(page.getByText('0/2 passed')).toBeInTheDocument();
		await page.getByText('0/2 passed').click();

		await expect.element(page.getByText('score 0.50')).toBeInTheDocument();
		await expect.element(page.getByText('The reply drifted off-topic.')).toBeInTheDocument();
		await expect.element(page.getByText('Provider timed out')).toBeInTheDocument();
	});

	it('fills the suite description, toggles the scope radios, and cancels without creating', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByPlaceholder('optional').fill('Catches greeting regressions');

		await page.getByRole('radio', { name: 'Workflow' }).click();
		await expect.element(page.getByText('Select a workflow…')).toBeInTheDocument();
		await page.getByRole('combobox').selectOptions('wf-1');

		await page.getByRole('radio', { name: 'Agent' }).click();
		await expect.element(page.getByText('Select an agent…')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect.element(page.getByRole('button', { name: '+ New suite' })).toBeInTheDocument();
		expect(evals.createSuite).not.toHaveBeenCalled();
	});

	it('shows a suite description when present', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([
			{ ...agentSuite, description: 'Covers the onboarding greeting flow.' }
		]);

		render(EvalsPage);

		await expect
			.element(page.getByText('Covers the onboarding greeting flow.'))
			.toBeInTheDocument();
	});
});
