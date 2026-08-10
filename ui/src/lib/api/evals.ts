// Eval suite resource client (#95, api/evals.py). Suite/case definitions
// are read/write; runs/results are read-only here, produced by POST .../run.

import { api } from './client';
import { auth } from './auth.svelte';

export type JudgeType = 'exact' | 'substring' | 'llm_judge' | 'structural';
export type EvalRunStatus = 'running' | 'completed';
export type EvalCaseResultStatus = 'passed' | 'failed' | 'error';

export interface EvalSuite {
	id: string;
	name: string;
	description: string | null;
	agent_id: string | null;
	workflow_id: string | null;
	subject_type: 'agent' | 'workflow';
	subject_name: string;
	case_count: number;
	created_at: string;
	updated_at: string;
}

export interface EvalCase {
	id: string;
	suite_id: string;
	name: string;
	input_content: string;
	judge_type: JudgeType;
	expected_output: string | null;
	rubric: string | null;
	expected_tool_name: string | null;
	expected_tool_args: Record<string, unknown> | null;
}

export interface EvalRun {
	id: string;
	suite_id: string;
	status: EvalRunStatus;
	triggered_by: string;
	triggered_by_id: string | null;
	case_count: number;
	pass_count: number;
	fail_count: number;
	error_count: number;
	started_at: string;
	completed_at: string | null;
}

export interface EvalCaseResult {
	id: string;
	run_id: string;
	case_id: string;
	status: EvalCaseResultStatus;
	score: number | null;
	actual_output: string | null;
	actual_tool_calls: { tool_name: string; tool_args: Record<string, unknown> | null }[] | null;
	judge_reasoning: string | null;
	error_message: string | null;
	started_at: string;
	completed_at: string | null;
}

export interface EvalSuiteCreateInput {
	name: string;
	description?: string;
	agent_id?: string;
	workflow_id?: string;
}

export interface EvalCaseCreateInput {
	name: string;
	input_content: string;
	judge_type: JudgeType;
	expected_output?: string;
	rubric?: string;
	expected_tool_name?: string;
	expected_tool_args?: Record<string, unknown>;
}

export const evals = {
	listSuites: () => api.get<EvalSuite[]>('/evals/suites', auth.token ?? undefined),
	createSuite: (body: EvalSuiteCreateInput) =>
		api.post<EvalSuite>('/evals/suites', body, auth.token ?? undefined),
	updateSuite: (suiteId: string, body: { name?: string; description?: string }) =>
		api.patch<EvalSuite>(`/evals/suites/${suiteId}`, body, auth.token ?? undefined),
	deleteSuite: (suiteId: string) => api.delete(`/evals/suites/${suiteId}`, auth.token ?? undefined),

	listCases: (suiteId: string) =>
		api.get<EvalCase[]>(`/evals/suites/${suiteId}/cases`, auth.token ?? undefined),
	createCase: (suiteId: string, body: EvalCaseCreateInput) =>
		api.post<EvalCase>(`/evals/suites/${suiteId}/cases`, body, auth.token ?? undefined),
	deleteCase: (suiteId: string, caseId: string) =>
		api.delete(`/evals/suites/${suiteId}/cases/${caseId}`, auth.token ?? undefined),

	run: (suiteId: string) =>
		api.post<EvalRun>(`/evals/suites/${suiteId}/run`, {}, auth.token ?? undefined),
	listRuns: (suiteId: string) =>
		api.get<EvalRun[]>(`/evals/suites/${suiteId}/runs`, auth.token ?? undefined),
	listResults: (suiteId: string, runId: string) =>
		api.get<EvalCaseResult[]>(
			`/evals/suites/${suiteId}/runs/${runId}/results`,
			auth.token ?? undefined
		)
};
