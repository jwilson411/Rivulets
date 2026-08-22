// Custom tool CRUD + versioning client (FR-8.2 through FR-8.4,
// server/api/tools.py). Simple-mode codegen (FR-8.3, #517) is generateCode():
// the server answers with a draft to review, and approval reuses create() +
// saveVersion() — see generateCode()'s doc comment.

import { api } from './client';
import { auth } from './auth.svelte';

export interface Tool {
	id: string;
	name: string;
	description: string;
	tool_type: 'builtin' | 'mcp' | 'custom';
	source_path: string | null;
	// #100: real blast radius (code exec, outbound HTTP, filesystem
	// writes, DB access) -- gates unattended use via an agent's
	// approved_for_unattended_tools flag. Read-only; v1 only marks the
	// fixed builtin set, not user-created custom/mcp tools.
	sensitive: boolean;
	// #188/#344: the capability scope (if any) an agent must hold via
	// PUT /agents/{id}/tool-scopes before this tool actually resolves for
	// it, even once assigned -- null means assignment alone is enough,
	// same as every tool before #188.
	required_scope: string | null;
	available: boolean;
	// #422: human picker label + group (chat / files / workspace_admin /
	// custom / mcp). Derived server-side from the tool name and type.
	display_name: string;
	group: string;
}

export interface ToolVersion {
	version: number;
	source_code: string;
	created_at: string;
}

export interface ToolCreateInput {
	name: string;
	description: string;
	mode: 'simple' | 'advanced';
	prompt?: string;
}

// The mode="simple" answer: generated source for the user to review.
// Nothing was created server-side — no id, on purpose.
export interface ToolCodegenDraft {
	source_code: string;
}

export interface ToolCodegenInput {
	name: string;
	description: string;
	prompt: string;
}

export interface ToolUpdateInput {
	name?: string;
	description?: string;
}

export const tools = {
	list: () => api.get<Tool[]>('/tools', auth.token ?? undefined),
	// #188/#344: the fixed catalog of capability scopes an owner can grant
	// to an agent (PUT /agents/{id}/tool-scopes) -- drives the "Advanced:
	// capability scopes" picker on the agents page.
	listScopes: () => api.get<string[]>('/tools/scopes', auth.token ?? undefined),
	get: (id: string) => api.get<Tool>(`/tools/${id}`, auth.token ?? undefined),
	// Advanced mode creates an empty custom tool ready for
	// saveVersion()/open-editor — also the "approve" half of the simple-mode
	// flow (generateCode below), which registers the reviewed code via those
	// same two calls.
	create: (body: ToolCreateInput) => api.post<Tool>('/tools', body, auth.token ?? undefined),
	// Simple mode (FR-8.3, #517): the server generates Agno tool code from
	// the description and returns it as a draft — the tool itself is only
	// created when the user approves (create() + saveVersion()). An older
	// server without codegen wired up still answers 501 here.
	generateCode: (body: ToolCodegenInput) =>
		api.post<ToolCodegenDraft>('/tools', { ...body, mode: 'simple' }, auth.token ?? undefined),
	update: (id: string, body: ToolUpdateInput) =>
		api.patch<Tool>(`/tools/${id}`, body, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/tools/${id}`, auth.token ?? undefined),
	listVersions: (id: string) =>
		api.get<ToolVersion[]>(`/tools/${id}/versions`, auth.token ?? undefined),
	saveVersion: (id: string, sourceCode: string) =>
		api.post<ToolVersion>(
			`/tools/${id}/versions`,
			{ source_code: sourceCode },
			auth.token ?? undefined
		),
	rollback: (id: string, version: number) =>
		api.post<Tool>(`/tools/${id}/versions/${version}/rollback`, undefined, auth.token ?? undefined),
	openEditor: (id: string) =>
		api.post<{ path: string }>(`/tools/${id}/open-editor`, undefined, auth.token ?? undefined)
};
