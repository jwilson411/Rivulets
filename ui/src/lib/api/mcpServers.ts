// MCP server discovery client (FR-8.5, api-design.md#mcp-servers).
// Registering a server always persists the row, even if the connection
// fails — a failed server just comes back with connected: false and no
// tools; POST .../reconnect retries (server/api/mcp_servers.py).

import { api } from './client';
import { auth } from './auth.svelte';

export interface MCPTool {
	id: string;
	name: string;
	description: string;
	mcp_tool_name: string | null;
	// Raw JSON Schema the MCP server advertised for this tool's arguments —
	// can express conditional/dependent structure via if/then/else,
	// dependentRequired, dependentSchemas, oneOf, etc.
	input_schema: Record<string, unknown>;
}

export interface MCPServer {
	id: string;
	name: string;
	url: string;
	connected: boolean;
	last_connected_at: string | null;
	// Names of configured auth headers only -- values are secrets and never
	// leave the server (server/api/mcp_servers.py's module docstring).
	header_names: string[];
}

export interface MCPServerDetail extends MCPServer {
	tools: MCPTool[];
}

export interface MCPServerCreateInput {
	name: string;
	url: string;
	// Setting headers at creation requires an owner session (server-side
	// 403 for an invite-grant session) -- entering auth on the workspace's
	// behalf, same as provider API keys.
	headers?: Record<string, string>;
}

export const mcpServers = {
	list: () => api.get<MCPServer[]>('/mcp-servers', auth.token ?? undefined),
	get: (id: string) => api.get<MCPServerDetail>(`/mcp-servers/${id}`, auth.token ?? undefined),
	create: (body: MCPServerCreateInput) =>
		api.post<MCPServerDetail>('/mcp-servers', body, auth.token ?? undefined),
	// Always a full replace -- pass {} to clear every header. Owner-only.
	setHeaders: (id: string, headers: Record<string, string>) =>
		api.put<MCPServerDetail>(`/mcp-servers/${id}/headers`, { headers }, auth.token ?? undefined),
	reconnect: (id: string) =>
		api.post<MCPServerDetail>(`/mcp-servers/${id}/reconnect`, undefined, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/mcp-servers/${id}`, auth.token ?? undefined)
};
