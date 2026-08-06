// Custom tool CRUD + versioning client (FR-8.2 through FR-8.4,
// server/api/tools.py). Simple-mode codegen (FR-8.3) is wired here but the
// backend still 501s until an LLM client is hooked up server-side — see
// create()'s doc comment.

import { api } from './client';
import { auth } from './auth.svelte';

export interface Tool {
	id: string;
	name: string;
	description: string;
	tool_type: 'builtin' | 'mcp' | 'custom';
	source_path: string | null;
	available: boolean;
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

export interface ToolUpdateInput {
	name?: string;
	description?: string;
}

export const tools = {
	list: () => api.get<Tool[]>('/tools', auth.token ?? undefined),
	get: (id: string) => api.get<Tool>(`/tools/${id}`, auth.token ?? undefined),
	// Simple mode (FR-8.3) currently always rejects with 501 — the codegen
	// path isn't wired up server-side yet. Advanced mode creates an empty
	// custom tool ready for saveVersion()/open-editor.
	create: (body: ToolCreateInput) => api.post<Tool>('/tools', body, auth.token ?? undefined),
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
