import { describe, expect, it } from 'vitest';
import type { Tool } from '$lib/api/tools';
import {
	defaultNewAgentScopes,
	defaultNewAgentToolIds,
	humanizeToolName,
	inviteGrantMayAssignTool,
	isGoogleIntegrationTool,
	SETTINGS_INTEGRATIONS_HREF,
	SETTINGS_INTEGRATIONS_SEARCH,
	toolDescriptionLine,
	toolDisplayName,
	toolGroup,
	toolsByGroup
} from './toolCatalog';

function tool(overrides: Partial<Tool> & Pick<Tool, 'id' | 'name'>): Tool {
	return {
		description: '',
		tool_type: 'builtin',
		source_path: null,
		sensitive: false,
		required_scope: null,
		available: true,
		display_name: '',
		group: '',
		...overrides
	};
}

describe('humanizeToolName', () => {
	it('turns snake_case into a sentence-style label', () => {
		expect(humanizeToolName('web_search')).toBe('Web search');
		expect(humanizeToolName('update_agent_peer_preference')).toBe('Update agent peer preference');
	});

	it('keeps HTTP, MCP, DB, and Python casing', () => {
		expect(humanizeToolName('http_request')).toBe('HTTP request');
		expect(humanizeToolName('execute_python')).toBe('Execute Python');
		expect(humanizeToolName('query_workspace_db')).toBe('Query workspace DB');
		expect(humanizeToolName('list_mcp_servers')).toBe('List MCP servers');
		expect(humanizeToolName('google_gmail_search')).toBe('Google Gmail search');
	});
});

describe('toolDisplayName / toolGroup', () => {
	it('prefers API-provided labels when present', () => {
		const listed = tool({
			id: '1',
			name: 'http_request',
			display_name: 'HTTP request',
			group: 'chat'
		});
		expect(toolDisplayName(listed)).toBe('HTTP request');
		expect(toolGroup(listed)).toBe('chat');
	});

	it('falls back from the identifier when the API omitted labels', () => {
		expect(toolDisplayName(tool({ id: '1', name: 'http_request' }))).toBe('HTTP request');
		expect(toolGroup(tool({ id: '1', name: 'http_request' }))).toBe('chat');
		expect(toolGroup(tool({ id: '2', name: 'read_file' }))).toBe('files');
		expect(toolGroup(tool({ id: '3', name: 'create_agent' }))).toBe('workspace_admin');
		expect(toolGroup(tool({ id: '5', name: 'google_gmail_search' }))).toBe('integrations');
		expect(toolGroup(tool({ id: '4', name: 'mine', tool_type: 'custom' }))).toBe('custom');
	});
});

describe('isGoogleIntegrationTool', () => {
	it('marks Gmail and Calendar tools, not model-provider tools', () => {
		expect(isGoogleIntegrationTool('google_gmail_search')).toBe(true);
		expect(isGoogleIntegrationTool('google_calendar_list')).toBe(true);
		expect(isGoogleIntegrationTool('web_search')).toBe(false);
		expect(SETTINGS_INTEGRATIONS_SEARCH).toBe('?tab=integrations');
		expect(SETTINGS_INTEGRATIONS_HREF).toBe('/settings?tab=integrations');
	});
});

describe('toolDescriptionLine', () => {
	it('keeps a one-sentence description', () => {
		expect(toolDescriptionLine('Search the web via Brave Search.')).toBe(
			'Search the web via Brave Search.'
		);
	});

	it('takes the first sentence of a longer docstring, including wrapped lines', () => {
		expect(
			toolDescriptionLine(
				'Read the content of a file attached to a rivulet message, given its\nfile_id (as returned by the file upload API). Text files are returned as text.'
			)
		).toBe(
			'Read the content of a file attached to a rivulet message, given its file_id (as returned by the file upload API).'
		);
	});
});

describe('inviteGrantMayAssignTool', () => {
	it('allows unscoped, non-sensitive builtins and refuses the rest', () => {
		expect(inviteGrantMayAssignTool(tool({ id: 'a', name: 'web_search' }))).toBe(true);
		expect(
			inviteGrantMayAssignTool(
				tool({
					id: 'b',
					name: 'http_request',
					sensitive: true,
					required_scope: 'sensitive_tools:manage'
				})
			)
		).toBe(false);
		expect(
			inviteGrantMayAssignTool(
				tool({ id: 'c', name: 'create_agent', required_scope: 'agents_teams:manage' })
			)
		).toBe(false);
		expect(inviteGrantMayAssignTool(tool({ id: 'd', name: 'mine', tool_type: 'custom' }))).toBe(
			false
		);
		expect(
			inviteGrantMayAssignTool(tool({ id: 'e', name: 'remote_thing', tool_type: 'mcp' }))
		).toBe(false);
	});
});

describe('defaultNewAgentToolIds / defaultNewAgentScopes', () => {
	it('starts a new agent with every listed tool and every scope', () => {
		expect(
			defaultNewAgentToolIds([
				tool({ id: 'a', name: 'web_search' }),
				tool({ id: 'b', name: 'execute_python' })
			])
		).toEqual(['a', 'b']);
		expect(defaultNewAgentScopes(['channels:manage', 'settings:manage'])).toEqual([
			'channels:manage',
			'settings:manage'
		]);
	});

	it('leaves the Google write permission off (#463)', () => {
		expect(
			defaultNewAgentToolIds([
				tool({
					id: 'search',
					name: 'google_gmail_search',
					required_scope: 'integrations:google'
				}),
				tool({
					id: 'send',
					name: 'google_gmail_send',
					required_scope: 'integrations:google:write'
				})
			])
		).toEqual(['search', 'send']);
		expect(defaultNewAgentScopes(['integrations:google', 'integrations:google:write'])).toEqual([
			'integrations:google'
		]);
	});

	it('starts an invite-grant agent with only assignable tools and no scopes (#472)', () => {
		expect(
			defaultNewAgentToolIds(
				[
					tool({ id: 'a', name: 'web_search' }),
					tool({
						id: 'b',
						name: 'http_request',
						sensitive: true,
						required_scope: 'sensitive_tools:manage'
					}),
					tool({ id: 'c', name: 'mine', tool_type: 'custom' })
				],
				'invite'
			)
		).toEqual(['a']);
		expect(defaultNewAgentScopes(['channels:manage', 'settings:manage'], 'invite')).toEqual([]);
	});
});

describe('toolsByGroup', () => {
	it('orders Chat, Files, Workspace admin and drops empty groups', () => {
		const grouped = toolsByGroup([
			tool({ id: 'a', name: 'create_agent' }),
			tool({ id: 'b', name: 'web_search' }),
			tool({ id: 'c', name: 'read_file' })
		]);
		expect(grouped.map((g) => g.label)).toEqual(['Chat', 'Files', 'Workspace admin']);
		expect(grouped[0]?.tools.map((t) => t.name)).toEqual(['web_search']);
	});
});
