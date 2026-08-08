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
}

export interface WorkflowConnection {
	id: string;
	workflow_id: string;
	from_node_id: string | null;
	to_node_id: string;
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
}

export interface WorkflowNodeCreateInput {
	name: string;
	node_type: WorkflowNodeType;
	agent_id?: string | null;
	child_workflow_id?: string | null;
	config?: Record<string, unknown>;
	retry_max_attempts?: number;
	retry_backoff_seconds?: number;
}

export interface WorkflowNodeUpdateInput {
	name?: string;
	agent_id?: string | null;
	child_workflow_id?: string | null;
	config?: Record<string, unknown>;
	retry_max_attempts?: number;
	retry_backoff_seconds?: number;
}

export interface WorkflowConnectionCreateInput {
	from_node_id: string | null;
	to_node_id: string;
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
	removeConnection: (workflowId: string, connectionId: string) =>
		api.delete<void>(
			`/workflows/${workflowId}/connections/${connectionId}`,
			auth.token ?? undefined
		),

	listRuns: (workflowId: string) =>
		api.get<WorkflowRun[]>(`/workflows/${workflowId}/runs`, auth.token ?? undefined),
	listNodeRuns: (workflowId: string, runId: string) =>
		api.get<WorkflowNodeRun[]>(
			`/workflows/${workflowId}/runs/${runId}/node-runs`,
			auth.token ?? undefined
		)
};
