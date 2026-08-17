// #422: human labels and grouping for the agent Tools picker. The API
// already sends `display_name` / `group` (server tool_catalog.py); these
// helpers order groups, fall back if a field is missing, and collapse a
// multi-line docstring to the one line the checkbox row can show.

import type { Tool } from '$lib/api/tools';

export const TOOL_GROUPS = [
	{ key: 'chat', label: 'Chat' },
	{ key: 'files', label: 'Files' },
	{ key: 'workspace_admin', label: 'Workspace admin' },
	{ key: 'custom', label: 'Yours' },
	{ key: 'mcp', label: 'From MCP' }
] as const;

const CHAT_TOOLS = new Set([
	'http_request',
	'read_attached_file',
	'search_knowledge_base',
	'web_search'
]);

const FILES_TOOLS = new Set(['execute_python', 'list_files', 'read_file', 'write_file']);

const TOKEN_OVERRIDES: Record<string, string> = {
	db: 'DB',
	http: 'HTTP',
	mcp: 'MCP',
	python: 'Python'
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

export function toolsByGroup(tools: Tool[]): { key: string; label: string; tools: Tool[] }[] {
	return TOOL_GROUPS.map((group) => ({
		...group,
		tools: tools.filter((tool) => toolGroup(tool) === group.key)
	})).filter((group) => group.tools.length > 0);
}
