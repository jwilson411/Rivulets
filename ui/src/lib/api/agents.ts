// Agent resource client (FR-3, api-design.md#agents).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Agent {
	id: string;
	name: string;
	description: string;
	instructions: string;
	model: string;
	// Ordered 'provider:model_name' strings (#103): tried in turn if
	// `model`'s call fails with a retryable-looking error.
	fallback_models: string[];
	// #107: a raw JSON Schema object constraining this agent's reply, or
	// null for free-form text (the only behavior before this existed).
	// Optional (not just nullable) so the many existing fixtures/mocks
	// across the UI test suite that predate this field don't all need
	// updating -- unlike fallback_models, most agents never set this.
	output_schema?: Record<string, unknown> | null;
	// #100: one-time approval to run this agent's sensitive tools (if any
	// are assigned) unattended -- schedules, remediation runs. Doesn't
	// affect ordinary chat/slash-command tool use at all.
	approved_for_unattended_tools: boolean;
	agentos_agent_id: string | null;
}

export type RuleType = 'keyword' | 'regex' | 'semantic' | 'always' | 'mention_only';

export interface RoutingRule {
	id: string;
	rule_type: RuleType;
	pattern: string;
	priority: number;
}

// Instructions/model history (#104) — a change to either field is
// snapshotted on create/update/rollback, mirroring ToolVersion.
export interface AgentVersion {
	version: number;
	instructions: string;
	model: string;
	created_at: string;
}

export interface AgentCreateInput {
	name: string;
	description: string;
	instructions: string;
	model: string;
	fallback_models?: string[];
	output_schema?: Record<string, unknown> | null;
	tool_ids?: string[];
	team_ids?: string[];
}

export interface AgentUpdateInput {
	name?: string;
	description?: string;
	instructions?: string;
	model?: string;
	fallback_models?: string[];
	output_schema?: Record<string, unknown> | null;
	tool_ids?: string[];
	team_ids?: string[];
	approved_for_unattended_tools?: boolean;
}

export const agents = {
	list: () => api.get<Agent[]>('/agents', auth.token ?? undefined),
	get: (id: string) => api.get<Agent>(`/agents/${id}`, auth.token ?? undefined),
	create: (body: AgentCreateInput) => api.post<Agent>('/agents', body, auth.token ?? undefined),
	update: (id: string, patch: AgentUpdateInput) =>
		api.patch<Agent>(`/agents/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/agents/${id}`, auth.token ?? undefined),
	getRoutingRules: (id: string) =>
		api.get<RoutingRule[]>(`/agents/${id}/routing-rules`, auth.token ?? undefined),
	setRoutingRules: (
		id: string,
		rules: { rule_type: RuleType; pattern: string; priority?: number }[]
	) => api.patch<RoutingRule[]>(`/agents/${id}/routing-rules`, { rules }, auth.token ?? undefined),
	getPeerPreference: (id: string) =>
		api.get<{ capability_tag: string | null }>(
			`/agents/${id}/peer-preference`,
			auth.token ?? undefined
		),
	setPeerPreference: (id: string, capability_tag: string | null) =>
		api.put<{ capability_tag: string | null }>(
			`/agents/${id}/peer-preference`,
			{ capability_tag },
			auth.token ?? undefined
		),
	listVersions: (id: string) =>
		api.get<AgentVersion[]>(`/agents/${id}/versions`, auth.token ?? undefined),
	rollback: (id: string, version: number) =>
		api.post<Agent>(
			`/agents/${id}/versions/${version}/rollback`,
			undefined,
			auth.token ?? undefined
		),
	// #344: the read counterpart to tool_ids on create/update -- lets a
	// picker (AgentForm's Tools section) show what's currently assigned.
	getToolIds: (id: string) =>
		api.get<{ tool_ids: string[] }>(`/agents/${id}/tools`, auth.token ?? undefined),
	// #188/#344: capability-scope grant/revoke (PUT is owner-only
	// server-side -- api/agents.py's set_agent_tool_scopes) -- previously
	// unreachable from the UI at all despite the HTTP endpoint existing.
	getToolScopes: (id: string) =>
		api.get<{ scopes: string[] }>(`/agents/${id}/tool-scopes`, auth.token ?? undefined),
	setToolScopes: (id: string, scopes: string[]) =>
		api.put<{ scopes: string[] }>(`/agents/${id}/tool-scopes`, { scopes }, auth.token ?? undefined)
};
