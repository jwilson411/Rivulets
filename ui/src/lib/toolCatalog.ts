// #422: human labels and grouping for the agent Tools picker. The API
// already sends `display_name` / `group` (server tool_catalog.py); these
// helpers order groups, fall back if a field is missing, and collapse a
// multi-line docstring to the one line the checkbox row can show.

import type { Tool } from '$lib/api/tools';

export const TOOL_GROUPS = [
	{ key: 'chat', label: 'Chat' },
	{ key: 'files', label: 'Files' },
	{ key: 'integrations', label: 'Integrations' },
	{ key: 'workspace_admin', label: 'Workspace admin' },
	{ key: 'custom', label: 'Yours' },
	{ key: 'mcp', label: 'From MCP' }
] as const;

const CHAT_TOOLS = new Set([
	'fetch_webpage',
	'http_request',
	'read_attached_file',
	'search_knowledge_base',
	'web_search'
]);

const FILES_TOOLS = new Set(['execute_python', 'list_files', 'read_file', 'write_file']);

const INTEGRATION_TOOLS = new Set([
	'google_calendar_create',
	'google_calendar_list',
	'google_calendar_update',
	'google_contacts_search',
	'google_docs_append',
	'google_docs_read',
	'google_drive_read',
	'google_drive_search',
	'google_drive_write',
	'google_gmail_draft',
	'google_gmail_read',
	'google_gmail_search',
	'google_gmail_send',
	'google_meet_create',
	'google_sheets_read',
	'google_sheets_update',
	'google_tasks_add',
	'google_tasks_list'
]);

// Google Workspace tools talk to a connected account, not the Google AI
// (Gemini) provider key. Agent picker rows link here when none is
// connected (#471).
export const SETTINGS_INTEGRATIONS_SEARCH = '?tab=integrations';
export const SETTINGS_INTEGRATIONS_HREF = `/settings${SETTINGS_INTEGRATIONS_SEARCH}`;

export function isGoogleIntegrationTool(name: string): boolean {
	return INTEGRATION_TOOLS.has(name);
}

const TOKEN_OVERRIDES: Record<string, string> = {
	calendar: 'Calendar',
	contacts: 'Contacts',
	db: 'DB',
	docs: 'Docs',
	drive: 'Drive',
	gmail: 'Gmail',
	google: 'Google',
	http: 'HTTP',
	mcp: 'MCP',
	meet: 'Meet',
	python: 'Python',
	sheets: 'Sheets',
	tasks: 'Tasks'
};

export function toolDisplayName(tool: Pick<Tool, 'name' | 'display_name'>): string {
	if (tool.display_name) return tool.display_name;
	return humanizeToolName(tool.name);
}

export function toolGroup(tool: Pick<Tool, 'name' | 'tool_type' | 'group'>): string {
	if (tool.group) return tool.group;
	if (tool.tool_type === 'custom') return 'custom';
	if (tool.tool_type === 'mcp') return 'mcp';
	if (CHAT_TOOLS.has(tool.name)) return 'chat';
	if (FILES_TOOLS.has(tool.name)) return 'files';
	if (INTEGRATION_TOOLS.has(tool.name)) return 'integrations';
	return 'workspace_admin';
}

export function toolDescriptionLine(description: string): string {
	const trimmed = description.trim();
	if (!trimmed) return '';
	const collapsed = trimmed
		.split('\n')
		.map((line) => line.trim())
		.filter(Boolean)
		.join(' ');
	const match = collapsed.match(/^.*?[.!?](?=\s|$)/);
	return (match?.[0] ?? collapsed).trim();
}

export function humanizeToolName(name: string): string {
	const words = name
		.split('_')
		.filter(Boolean)
		.map((part, index) => {
			const override = TOKEN_OVERRIDES[part.toLowerCase()];
			if (override) return override;
			if (index === 0) return part.slice(0, 1).toUpperCase() + part.slice(1).toLowerCase();
			return part.toLowerCase();
		});
	return words.join(' ') || name;
}

// #476: agent Permissions is the same catalog as TOOL_SCOPES, shown as
// sentences so "uncheck what you want to hold back" is readable. Slug
// stays the value the API stores.
const SCOPE_LABELS: Record<string, string> = {
	'channels:manage': 'Manage channels',
	'agents_teams:manage': 'Manage agents and teams',
	'mcp_servers:manage': 'Manage MCP servers',
	'workflows:manage': 'Manage workflows',
	'settings:manage': 'Manage settings',
	'invites:manage': 'Manage invites',
	'sensitive_tools:manage': 'Sensitive tools',
	'integrations:google': 'Google Workspace',
	'integrations:google:write': 'Google send, draft, and write'
};

export function scopeDisplayName(scope: string): string {
	if (SCOPE_LABELS[scope]) return SCOPE_LABELS[scope];
	const parts = scope.split(':').filter(Boolean);
	if (parts.length === 0) return scope;
	const verb = parts[parts.length - 1];
	const resource = humanizeToolName(parts.slice(0, -1).join('_'));
	if (verb === 'manage' && resource) return `Manage ${resource.toLowerCase()}`;
	if ((verb === 'write' || verb === 'read') && resource) {
		return `${resource} ${verb}`;
	}
	return parts.map((part) => humanizeToolName(part)).join(' ') || scope;
}

export function toolsByGroup(tools: Tool[]): { key: string; label: string; tools: Tool[] }[] {
	return TOOL_GROUPS.map((group) => ({
		...group,
		tools: tools.filter((tool) => toolGroup(tool) === group.key)
	})).filter((group) => group.tools.length > 0);
}

// #472: mirrors find_unauthorized_tool_assignment on the server — an
// invite-grant session 403s if it tries to assign a scoped, sensitive,
// custom, or MCP tool. `sensitive` is the API's flag for the builtin
// blast-radius set; custom/MCP are blocked by type even when that flag
// is false.
export function inviteGrantMayAssignTool(
	tool: Pick<Tool, 'tool_type' | 'required_scope' | 'sensitive'>
): boolean {
	if (tool.tool_type === 'custom' || tool.tool_type === 'mcp') return false;
	if (tool.required_scope != null) return false;
	return !tool.sensitive;
}

// #463: connecting Google must not enable send-as-me just because a
// new agent starts with every other tool and permission checked.
export const DEFAULT_WITHHELD_SCOPES = new Set(['integrations:google:write']);

export function defaultNewAgentToolIds(tools: Tool[], grant: string | null = 'owner'): string[] {
	const pool = grant === 'owner' ? tools : tools.filter(inviteGrantMayAssignTool);
	return pool.map((tool) => tool.id);
}

export function defaultNewAgentScopes(scopes: string[], grant: string | null = 'owner'): string[] {
	return grant === 'owner' ? scopes.filter((scope) => !DEFAULT_WITHHELD_SCOPES.has(scope)) : [];
}
