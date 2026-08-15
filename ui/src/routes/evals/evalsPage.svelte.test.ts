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

// #355: the page filters draft workflows out of the subject picker for
// invite-grant sessions (the server 403s a guest suite create/run against
// an unpublished workflow). Same hoisted-getter mock as Sidebar's tests.
const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
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

const draftWorkflow: Workflow = {
	...digestWorkflow,
	id: 'wf-2',
	name: 'scratchpad',
	published: false
};

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
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

	it('shows an error in place of the suite list when deleting a suite fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.deleteSuite).mockRejectedValueOnce(new Error('Failed to delete eval suite'));

		render(EvalsPage);
		await expect.element(page.getByText('greeting-suite')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		expect(evals.deleteSuite).toHaveBeenCalledWith('suite-1');
		await expect.element(page.getByText('Failed to delete eval suite')).toBeInTheDocument();
	});

	it('collapses the cases panel when Manage cases is clicked again', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Hide cases' }).click();

		await expect.element(page.getByText('greets')).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Manage cases' })).toBeInTheDocument();
	});

	it('shows an error when cases fail to load', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockRejectedValueOnce(new Error('Failed to load cases'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();

		await expect.element(page.getByText('Failed to load cases')).toBeInTheDocument();
	});

	it('shows an error in place of the case list when deleting a case fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);
		vi.mocked(evals.deleteCase).mockRejectedValueOnce(new Error('Failed to delete case'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await expect.element(page.getByText('greets')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Remove' }).click();

		expect(evals.deleteCase).toHaveBeenCalledWith('suite-1', 'case-1');
		await expect.element(page.getByText('Failed to delete case')).toBeInTheDocument();
		// The cases panel itself (with its "+ Add case" affordance) stays open
		// even though the case list underneath it was replaced by the error.
		await expect.element(page.getByRole('button', { name: '+ Add case' })).toBeInTheDocument();
	});

	it("rejects invalid JSON in a structural case's expected tool args", async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		await page.getByRole('textbox').first().fill('calls search');
		await page.getByRole('textbox').nth(1).fill('find cats');
		await page.getByRole('combobox').selectOptions('structural');
		await page.getByRole('textbox').nth(2).fill('search');
		await page.getByRole('textbox').nth(3).fill('{not valid json');

		await page.getByRole('button', { name: 'Add case' }).click();

		await expect
			.element(page.getByText('Expected tool args must be valid JSON (or left blank).'))
			.toBeInTheDocument();
		expect(evals.createCase).not.toHaveBeenCalled();
	});

	it('creates a structural case with parsed tool args', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([]).mockResolvedValueOnce([exactCase]);
		vi.mocked(evals.createCase).mockResolvedValueOnce(exactCase);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		await page.getByRole('textbox').first().fill('calls search');
		await page.getByRole('textbox').nth(1).fill('find cats');
		await page.getByRole('combobox').selectOptions('structural');
		await page.getByRole('textbox').nth(2).fill('search');
		await page.getByRole('textbox').nth(3).fill('{"query": "cats"}');

		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'calls search',
			input_content: 'find cats',
			judge_type: 'structural',
			expected_output: undefined,
			rubric: undefined,
			expected_tool_name: 'search',
			expected_tool_args: { query: 'cats' }
		});
	});

	it('creates an LLM-judge case with a rubric', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValueOnce([]).mockResolvedValueOnce([exactCase]);
		vi.mocked(evals.createCase).mockResolvedValueOnce(exactCase);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		await page.getByRole('textbox').first().fill('polite reply');
		await page.getByRole('textbox').nth(1).fill('hi there');
		await page.getByRole('combobox').selectOptions('llm_judge');
		await page.getByRole('textbox').nth(2).fill('The reply should be friendly and on-topic.');

		await page.getByRole('button', { name: 'Add case' }).click();

		expect(evals.createCase).toHaveBeenCalledWith('suite-1', {
			name: 'polite reply',
			input_content: 'hi there',
			judge_type: 'llm_judge',
			expected_output: undefined,
			rubric: 'The reply should be friendly and on-topic.',
			expected_tool_name: undefined,
			expected_tool_args: undefined
		});
	});

	it('shows an error and keeps the form open when adding a case fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);
		vi.mocked(evals.createCase).mockRejectedValueOnce(new Error('Failed to add case'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();

		await page.getByRole('textbox').first().fill('greets');
		await page.getByRole('textbox').nth(1).fill('hi');
		await page.getByRole('textbox').nth(2).fill('hello');
		await page.getByRole('button', { name: 'Add case' }).click();

		await expect.element(page.getByText('Failed to add case')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Add case' })).toBeInTheDocument();
	});

	it('closes the add-case form via Cancel without creating a case', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: '+ Add case' }).click();
		await expect.element(page.getByRole('button', { name: 'Add case' })).toBeInTheDocument();

		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect.element(page.getByRole('button', { name: '+ Add case' })).toBeInTheDocument();
		expect(evals.createCase).not.toHaveBeenCalled();
	});

	it('shows an error when running a suite fails', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.run).mockRejectedValueOnce(new Error('Failed to run eval suite'));

		render(EvalsPage);
		await page.getByRole('button', { name: 'Run', exact: true }).click();

		await expect.element(page.getByText('Failed to run eval suite')).toBeInTheDocument();
	});

	it('shows the empty state and collapses run history when toggled again', async () => {
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
		await expect.element(page.getByText('1/1 passed')).toBeInTheDocument();

		await page.getByText('1/1 passed').click();

		await expect.element(page.getByText('Failed to load results')).toBeInTheDocument();
	});

	it('collapses run results and does not refetch them when re-expanded', async () => {
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

		// Expand -- fetches results.
		await page.getByText('0/1 passed').click();
		await expect.element(page.getByText('goodbye')).toBeInTheDocument();

		// Collapse.
		await page.getByText('0/1 passed').click();
		await expect.element(page.getByText('goodbye')).not.toBeInTheDocument();

		// Re-expand -- results are already cached, so no second fetch.
		await page.getByText('0/1 passed').click();
		await expect.element(page.getByText('goodbye')).toBeInTheDocument();
		expect(evals.listResults).toHaveBeenCalledTimes(1);
	});

	it('distinguishes passed and errored results, and shows a mixed-run summary badge', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([agentSuite]);
		vi.mocked(evals.listCases).mockResolvedValue([exactCase]);
		const run: EvalRun = {
			id: 'run-1',
			suite_id: 'suite-1',
			status: 'completed',
			triggered_by: 'human',
			triggered_by_id: null,
			case_count: 2,
			pass_count: 1,
			fail_count: 0,
			error_count: 1,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listRuns).mockResolvedValueOnce([run]);
		const passedResult: EvalCaseResult = {
			id: 'result-1',
			run_id: 'run-1',
			case_id: 'case-1',
			status: 'passed',
			score: 0.9,
			actual_output: 'hello',
			actual_tool_calls: null,
			judge_reasoning: 'Matches the expected greeting closely.',
			error_message: null,
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		const erroredResult: EvalCaseResult = {
			id: 'result-2',
			run_id: 'run-1',
			case_id: 'case-1',
			status: 'error',
			score: null,
			actual_output: null,
			actual_tool_calls: null,
			judge_reasoning: null,
			error_message: 'Judge provider timed out',
			started_at: '2026-01-01T00:00:00Z',
			completed_at: '2026-01-01T00:00:01Z'
		};
		vi.mocked(evals.listResults).mockResolvedValueOnce([passedResult, erroredResult]);

		render(EvalsPage);
		await page.getByRole('button', { name: 'Manage cases' }).click();
		await page.getByRole('button', { name: 'Run history' }).click();
		// Mixed run (some passed, some errored, none failed) uses the amber
		// "in-between" summary badge rather than all-pass or has-failures.
		await expect.element(page.getByText('1/2 passed')).toBeInTheDocument();

		await page.getByText('1/2 passed').click();

		await expect.element(page.getByText('passed', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('error', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('score 0.90')).toBeInTheDocument();
		await expect
			.element(page.getByText('Matches the expected greeting closely.'))
			.toBeInTheDocument();
		await expect.element(page.getByText('Judge provider timed out')).toBeInTheDocument();
	});

	it('creates a workflow-attached suite with a description', async () => {
		stubLists();
		const workflowSuite: EvalSuite = {
			...agentSuite,
			id: 'suite-2',
			name: 'digest-suite',
			description: 'Covers the nightly digest workflow',
			agent_id: null,
			workflow_id: 'wf-1',
			subject_type: 'workflow',
			subject_name: 'digest',
			case_count: 0
		};
		vi.mocked(evals.listSuites).mockResolvedValueOnce([]).mockResolvedValueOnce([workflowSuite]);
		vi.mocked(evals.createSuite).mockResolvedValueOnce(workflowSuite);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByPlaceholder('greeting-regressions').fill('digest-suite');
		await page.getByPlaceholder('optional').fill('Covers the nightly digest workflow');
		await page.getByRole('radio', { name: 'Workflow' }).click();
		await expect.element(page.getByText('Select a workflow…')).toBeInTheDocument();
		await page.getByRole('combobox').selectOptions('wf-1');
		await page.getByRole('button', { name: 'Create suite' }).click();

		expect(evals.createSuite).toHaveBeenCalledWith({
			name: 'digest-suite',
			description: 'Covers the nightly digest workflow',
			agent_id: undefined,
			workflow_id: 'wf-1'
		});
		await expect.element(page.getByText('digest-suite')).toBeInTheDocument();

		// Also shows the suite's description once listed.
		await expect.element(page.getByText('Covers the nightly digest workflow')).toBeInTheDocument();
	});

	it('resets the chosen subject when switching scope from workflow back to agent', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();

		await page.getByRole('radio', { name: 'Workflow' }).click();
		await page.getByRole('combobox').selectOptions('wf-1');
		await expect.element(page.getByRole('combobox')).toHaveValue('wf-1');

		await page.getByRole('radio', { name: 'Agent' }).click();

		await expect.element(page.getByText('Select an agent…')).toBeInTheDocument();
		await expect.element(page.getByRole('combobox')).toHaveValue('');
	});

	it('closes the create-suite form via Cancel without creating a suite', async () => {
		stubLists();
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await expect.element(page.getByRole('button', { name: 'Create suite' })).toBeInTheDocument();

		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect
			.element(page.getByRole('button', { name: 'Create suite' }))
			.not.toBeInTheDocument();
		expect(evals.createSuite).not.toHaveBeenCalled();
	});

	it('offers draft workflows to an owner, labeled as drafts', async () => {
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(workflows.list).mockResolvedValue([digestWorkflow, draftWorkflow]);
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByRole('radio', { name: 'Workflow' }).click();

		await expect.element(page.getByRole('option', { name: '/digest' })).toBeInTheDocument();
		await expect
			.element(page.getByRole('option', { name: '/scratchpad (draft)' }))
			.toBeInTheDocument();
	});

	it('hides draft workflows from the subject picker for an invite-grant session', async () => {
		authState.grant = 'invite';
		vi.mocked(agents.list).mockResolvedValue([researcher]);
		vi.mocked(workflows.list).mockResolvedValue([digestWorkflow, draftWorkflow]);
		vi.mocked(evals.listSuites).mockResolvedValue([]);

		render(EvalsPage);
		await page.getByRole('button', { name: '+ New suite' }).click();
		await page.getByRole('radio', { name: 'Workflow' }).click();

		await expect.element(page.getByRole('option', { name: '/digest' })).toBeInTheDocument();
		await expect.element(page.getByRole('option', { name: /scratchpad/ })).not.toBeInTheDocument();
	});
});
