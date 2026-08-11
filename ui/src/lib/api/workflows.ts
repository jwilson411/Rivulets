// Workflow resource client (#80, api/workflows.py) -- definitions
// (Workflow/WorkflowNode/WorkflowConnection) are read/write; runs
// (WorkflowRun/WorkflowNodeRun) are read-only here, produced by the engine
// when a workflow is triggered via `/{name}` in a channel or the
// run_workflow tool (workflows/trigger.py), not via this client.

import { api } from './client';
import { auth } from './auth.svelte';

export type WorkflowNodeType =
	'agent' | 'summarize' | 'transform' | 'conditional' | 'merge' | 'human_input' | 'workflow';

export interface Workflow {
	id: string;
	name: string;
	description: string | null;
	published: boolean;
	// #94 layer 2: remediation workflow triggered automatically when a run
	// of this workflow fails. null = no remediation configured.
	on_failure_workflow_id: string | null;
	// #94 layer 3: agent @mentioned automatically when a run of this
	// workflow fails. null = falls back to the workspace-wide default
	// ('workflows.default_on_call_agent_id' in settings), not "disabled".
	on_call_agent_id: string | null;
	created_at: string;
	updated_at: string;
}

export interface WorkflowNode {
	id: string;
	workflow_id: string;
	name: string;
	node_type: WorkflowNodeType;
	agent_id: string | null;
	child_workflow_id: string | null;
	config: Record<string, unknown>;
	retry_max_attempts: number;
	retry_backoff_seconds: number;
	position_x: number | null;
	position_y: number | null;
}

export interface WorkflowConnection {
	id: string;
	workflow_id: string;
	from_node_id: string | null;
	to_node_id: string;
	condition_json: Record<string, unknown> | null;
}

export type WorkflowRunStatus = 'running' | 'awaiting_human' | 'completed' | 'failed' | 'stopped';

export interface WorkflowRun {
	id: string;
	workflow_id: string;
	rivulet_id: string;
	triggered_by: string;
	triggered_by_id: string | null;
	status: WorkflowRunStatus;
	current_node_id: string | null;
	error_message: string | null;
	final_output: string | null;
	started_at: string;
	completed_at: string | null;
}

export interface FailedWorkflowRun extends WorkflowRun {
	workflow_name: string;
	channel_id: string;
}

export interface WorkflowNodeRun {
	id: string;
	node_id: string;
	attempt: number;
	status: string;
	output_content: string | null;
	error_message: string | null;
	started_at: string;
	completed_at: string | null;
}

export interface WorkflowCreateInput {
	name: string;
	description?: string | null;
}

export interface WorkflowUpdateInput {
	name?: string;
	description?: string | null;
	// Omit entirely to leave unchanged; include (even as null, to clear
	// remediation/on-call) to update -- mirrors api/workflows.py's
	// WorkflowUpdate, the two fields there distinguishing "not provided"
	// from "set to null".
	on_failure_workflow_id?: string | null;
	on_call_agent_id?: string | null;
}

export interface WorkflowNodeCreateInput {
	name: string;
	node_type: WorkflowNodeType;
	agent_id?: string | null;
	child_workflow_id?: string | null;
	config?: Record<string, unknown>;
	retry_max_attempts?: number;
	retry_backoff_seconds?: number;
	position_x?: number;
	position_y?: number;
}

export interface WorkflowNodeUpdateInput {
	name?: string;
	agent_id?: string | null;
	child_workflow_id?: string | null;
	config?: Record<string, unknown>;
	retry_max_attempts?: number;
	retry_backoff_seconds?: number;
	position_x?: number;
	position_y?: number;
}

export interface WorkflowConnectionCreateInput {
	from_node_id: string | null;
	to_node_id: string;
	condition_json?: Record<string, unknown> | null;
}

export interface WorkflowConnectionUpdateInput {
	// Omit to leave unchanged; include (even as null, to clear the
	// condition) to update -- mirrors api/workflows.py's
	// WorkflowConnectionUpdate.
	condition_json?: Record<string, unknown> | null;
}

export interface WorkflowSchedule {
	id: string;
	workflow_id: string;
	channel_id: string;
	// null for a #93 one-off (run_once) schedule created via the
	// schedule_workflow agent tool -- next_fire_at is the fire time
	// itself, there's no recurring cron expression to show.
	cron_expression: string | null;
	run_once: boolean;
	input_content: string;
	enabled: boolean;
	next_fire_at: string;
	last_fired_at: string | null;
	consecutive_failures: number;
	// Optional short label (e.g. "daily digest"). 'human' vs. an agent id
	// distinguishes a schedule made through this builder from one an
	// agent created via chat -- the latter starts disabled pending
	// approval (#93).
	name: string | null;
	created_by: string;
	created_at: string;
	updated_at: string;
}

export interface WorkflowScheduleCreateInput {
	channel_id: string;
	cron_expression: string;
	input_content?: string;
	enabled?: boolean;
}

export interface WorkflowScheduleUpdateInput {
	channel_id?: string;
	cron_expression?: string;
	input_content?: string;
	enabled?: boolean;
}

export interface CronPreview {
	valid: boolean;
	next_fire_at: string | null;
	error: string | null;
}

// #99: an external system's HMAC-signed POST fires this workflow. Never
// carries the signing secret -- that's returned exactly once, from
// createWebhook/rotateWebhookSecret, as WorkflowWebhookCreated below.
export interface WorkflowWebhook {
	id: string;
	workflow_id: string;
	channel_id: string;
	name: string | null;
	// "{input}" substitution, same convention as a transform node's
	// template. null passes the raw request body through unchanged.
	input_template: string | null;
	enabled: boolean;
	last_triggered_at: string | null;
	created_at: string;
	updated_at: string;
}

// Only ever returned from create/rotate -- shown once, like an invite
// link, then never retrievable again.
export interface WorkflowWebhookCreated extends WorkflowWebhook {
	secret: string;
}

export interface WorkflowWebhookCreateInput {
	channel_id: string;
	name?: string;
	input_template?: string;
	enabled?: boolean;
}

export interface WorkflowWebhookUpdateInput {
	channel_id?: string;
	name?: string;
	input_template?: string;
	enabled?: boolean;
}

export const workflows = {
	list: () => api.get<Workflow[]>('/workflows', auth.token ?? undefined),
	get: (id: string) => api.get<Workflow>(`/workflows/${id}`, auth.token ?? undefined),
	create: (body: WorkflowCreateInput) =>
		api.post<Workflow>('/workflows', body, auth.token ?? undefined),
	update: (id: string, patch: WorkflowUpdateInput) =>
		api.patch<Workflow>(`/workflows/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/workflows/${id}`, auth.token ?? undefined),
	publish: (id: string) =>
		api.post<Workflow>(`/workflows/${id}/publish`, {}, auth.token ?? undefined),
	unpublish: (id: string) =>
		api.post<Workflow>(`/workflows/${id}/unpublish`, {}, auth.token ?? undefined),

	listNodes: (workflowId: string) =>
		api.get<WorkflowNode[]>(`/workflows/${workflowId}/nodes`, auth.token ?? undefined),
	createNode: (workflowId: string, body: WorkflowNodeCreateInput) =>
		api.post<WorkflowNode>(`/workflows/${workflowId}/nodes`, body, auth.token ?? undefined),
	updateNode: (workflowId: string, nodeId: string, patch: WorkflowNodeUpdateInput) =>
		api.patch<WorkflowNode>(
			`/workflows/${workflowId}/nodes/${nodeId}`,
			patch,
			auth.token ?? undefined
		),
	removeNode: (workflowId: string, nodeId: string) =>
		api.delete<void>(`/workflows/${workflowId}/nodes/${nodeId}`, auth.token ?? undefined),

	listConnections: (workflowId: string) =>
		api.get<WorkflowConnection[]>(`/workflows/${workflowId}/connections`, auth.token ?? undefined),
	createConnection: (workflowId: string, body: WorkflowConnectionCreateInput) =>
		api.post<WorkflowConnection>(
			`/workflows/${workflowId}/connections`,
			body,
			auth.token ?? undefined
		),
	updateConnection: (
		workflowId: string,
		connectionId: string,
		patch: WorkflowConnectionUpdateInput
	) =>
		api.patch<WorkflowConnection>(
			`/workflows/${workflowId}/connections/${connectionId}`,
			patch,
			auth.token ?? undefined
		),
	removeConnection: (workflowId: string, connectionId: string) =>
		api.delete<void>(
			`/workflows/${workflowId}/connections/${connectionId}`,
			auth.token ?? undefined
		),

	listSchedules: (workflowId: string) =>
		api.get<WorkflowSchedule[]>(`/workflows/${workflowId}/schedules`, auth.token ?? undefined),
	createSchedule: (workflowId: string, body: WorkflowScheduleCreateInput) =>
		api.post<WorkflowSchedule>(`/workflows/${workflowId}/schedules`, body, auth.token ?? undefined),
	updateSchedule: (workflowId: string, scheduleId: string, patch: WorkflowScheduleUpdateInput) =>
		api.patch<WorkflowSchedule>(
			`/workflows/${workflowId}/schedules/${scheduleId}`,
			patch,
			auth.token ?? undefined
		),
	removeSchedule: (workflowId: string, scheduleId: string) =>
		api.delete<void>(`/workflows/${workflowId}/schedules/${scheduleId}`, auth.token ?? undefined),
	previewSchedule: (workflowId: string, cronExpression: string) =>
		api.post<CronPreview>(
			`/workflows/${workflowId}/schedules/preview`,
			{ cron_expression: cronExpression },
			auth.token ?? undefined
		),

	listWebhooks: (workflowId: string) =>
		api.get<WorkflowWebhook[]>(`/workflows/${workflowId}/webhooks`, auth.token ?? undefined),
	createWebhook: (workflowId: string, body: WorkflowWebhookCreateInput) =>
		api.post<WorkflowWebhookCreated>(
			`/workflows/${workflowId}/webhooks`,
			body,
			auth.token ?? undefined
		),
	updateWebhook: (workflowId: string, webhookId: string, patch: WorkflowWebhookUpdateInput) =>
		api.patch<WorkflowWebhook>(
			`/workflows/${workflowId}/webhooks/${webhookId}`,
			patch,
			auth.token ?? undefined
		),
	removeWebhook: (workflowId: string, webhookId: string) =>
		api.delete<void>(`/workflows/${workflowId}/webhooks/${webhookId}`, auth.token ?? undefined),
	rotateWebhookSecret: (workflowId: string, webhookId: string) =>
		api.post<WorkflowWebhookCreated>(
			`/workflows/${workflowId}/webhooks/${webhookId}/rotate-secret`,
			{},
			auth.token ?? undefined
		),

	listRuns: (workflowId: string) =>
		api.get<WorkflowRun[]>(`/workflows/${workflowId}/runs`, auth.token ?? undefined),
	listFailedRuns: () =>
		api.get<FailedWorkflowRun[]>('/workflows/runs/failed', auth.token ?? undefined),
	listNodeRuns: (workflowId: string, runId: string) =>
		api.get<WorkflowNodeRun[]>(
			`/workflows/${workflowId}/runs/${runId}/node-runs`,
			auth.token ?? undefined
		)
};
