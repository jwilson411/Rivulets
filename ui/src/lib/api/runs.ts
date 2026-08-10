// Unified run tracing resource client (#96, api/runs.py). Read-only:
// entirely populated by tracing.py's instrumentation of dispatch/service.py
// and workflows/engine.py, nothing here is created through this client.

import { api } from './client';
import { auth } from './auth.svelte';

export type RunTriggerType = 'message' | 'schedule';
export type RunStatus = 'running' | 'completed' | 'error';
export type RunSpanType = 'dispatch_decision' | 'agent_run' | 'workflow_run' | 'workflow_node_run';

export interface RunTrace {
	id: string;
	trigger_type: RunTriggerType;
	label: string;
	rivulet_id: string | null;
	channel_id: string | null;
	status: RunStatus;
	span_count: number;
	total_cost_usd: number | null;
	total_tokens: number;
	started_at: string;
	completed_at: string | null;
}

// #100: recorded whenever a tool actually executes during an agent run --
// see agentos/tool_audit.py. Only populated on 'agent_run' spans.
export interface ToolCall {
	id: string;
	tool_name: string;
	sensitive: boolean;
	status: 'success' | 'error';
	arguments_json: string | null;
	result_summary: string | null;
	duration_ms: number | null;
	created_at: string;
}

export interface RunSpan {
	id: string;
	parent_span_id: string | null;
	span_type: RunSpanType;
	entity_id: string | null;
	name: string;
	status: RunStatus;
	model: string | null;
	cost_usd: number | null;
	total_tokens: number | null;
	started_at: string;
	completed_at: string | null;
	duration_ms: number | null;
	tool_calls: ToolCall[];
}

export interface RunTraceDetail extends RunTrace {
	spans: RunSpan[];
}

export const runs = {
	list: (limit?: number) =>
		api.get<RunTrace[]>(`/runs${limit ? `?limit=${limit}` : ''}`, auth.token ?? undefined),
	get: (traceId: string) => api.get<RunTraceDetail>(`/runs/${traceId}`, auth.token ?? undefined)
};
