// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/mcpServers, plus the real (unmocked)
// $lib/api/client for the ApiError class used in instanceof checks.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import McpServersPage from './+page.svelte';
import { mcpServers, type MCPServer, type MCPServerDetail } from '$lib/api/mcpServers';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/mcpServers', () => ({
	mcpServers: {
		list: vi.fn(),
		get: vi.fn(),
		create: vi.fn(),
		setHeaders: vi.fn(),
		setEnv: vi.fn(),
		reconnect: vi.fn(),
		remove: vi.fn()
	}
}));

const fsServerSummary: MCPServer = {
	id: 'mcp-1',
	name: 'Filesystem tools',
	transport: 'streamable-http',
	url: 'http://localhost:9001',
	header_names: [],
	command: null,
	args: [],
	env_names: [],
	connected: true,
	last_connected_at: '2026-08-01T00:00:00Z'
};

const fsServerDetail: MCPServerDetail = {
	...fsServerSummary,
	tools: [
		{
			id: 't-1',
			name: 'read_file',
			description: 'Reads a file',
			mcp_tool_name: 'read_file',
			input_schema: { type: 'object', properties: { path: { type: 'string' } } }
		}
	]
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('mcp-servers/+page.svelte', () => {
	it('lists servers with connection status and discovered tool count', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);

		render(McpServersPage);

		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		// Exact match: the "Status" filter select also has "Connected" /
		// "Disconnected" options, which a substring match would also hit.
		await expect.element(page.getByText('connected', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('1 tool')).toBeInTheDocument();
		await expect.element(page.getByText('read_file')).toBeInTheDocument();
	});

	it('shows a disconnected badge and no tool count for a disconnected server', async () => {
		const disconnected: MCPServerDetail = {
			...fsServerDetail,
			connected: false,
			tools: []
		};
		vi.mocked(mcpServers.list).mockResolvedValue([{ ...fsServerSummary, connected: false }]);
		vi.mocked(mcpServers.get).mockResolvedValue(disconnected);

		render(McpServersPage);

		await expect.element(page.getByText('disconnected', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText(/\d+ tools?/)).not.toBeInTheDocument();
	});

	it('flags a discovered tool whose schema has conditional/dependent args', async () => {
		const conditionalDetail: MCPServerDetail = {
			...fsServerDetail,
			tools: [
				{
					id: 't-2',
					name: 'send_message',
					description: 'Sends a message, optionally scheduled',
					mcp_tool_name: 'send_message',
					input_schema: {
						type: 'object',
						properties: { schedule_at: { type: 'string' } },
						dependentRequired: { schedule_at: ['timezone'] }
					}
				}
			]
		};
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(conditionalDetail);

		render(McpServersPage);

		await expect.element(page.getByText('send_message')).toBeInTheDocument();
		await expect.element(page.getByText('conditional args')).toBeInTheDocument();
	});

	it('does not flag a discovered tool with a plain, unconditional schema', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);

		render(McpServersPage);

		await expect.element(page.getByText('read_file')).toBeInTheDocument();
		await expect.element(page.getByText('conditional args')).not.toBeInTheDocument();
	});

	it('registers a server via mcpServers.create and clears the form', async () => {
		vi.mocked(mcpServers.list).mockResolvedValueOnce([]).mockResolvedValueOnce([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.create).mockResolvedValueOnce(fsServerDetail);

		render(McpServersPage);
		await expect
			.element(page.getByText('No MCP servers registered yet — add one above.'))
			.toBeInTheDocument();

		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Filesystem tools',
			transport: 'streamable-http',
			url: 'http://localhost:9001',
			headers: undefined
		});
		await expect.element(page.getByPlaceholder('Name (e.g. Filesystem tools)')).toHaveValue('');
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
	});

	it('registers a server with auth headers via mcpServers.create', async () => {
		vi.mocked(mcpServers.list).mockResolvedValueOnce([]).mockResolvedValueOnce([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.create).mockResolvedValueOnce(fsServerDetail);

		render(McpServersPage);
		await expect
			.element(page.getByText('No MCP servers registered yet — add one above.'))
			.toBeInTheDocument();

		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByText('Advanced: auth headers').click();
		await page
			.getByPlaceholder('Authorization: Bearer sk-...')
			.first()
			.fill('Authorization: Bearer sk-real-secret');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Filesystem tools',
			transport: 'streamable-http',
			url: 'http://localhost:9001',
			headers: { Authorization: 'Bearer sk-real-secret' }
		});
	});

	it('shows an error and does not submit when a header line has no colon', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByText('Advanced: auth headers').click();
		await page.getByPlaceholder('Authorization: Bearer sk-...').first().fill('not-a-header-line');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing ":"/)).toBeInTheDocument();
	});

	it('shows configured header names on a server with headers', async () => {
		const authedServer: MCPServerDetail = { ...fsServerDetail, header_names: ['Authorization'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...fsServerSummary, header_names: ['Authorization'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(authedServer);

		render(McpServersPage);

		await expect.element(page.getByText('Auth headers: Authorization')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Edit headers' })).toBeInTheDocument();
	});

	it('adds headers to an existing server via mcpServers.setHeaders', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.setHeaders).mockResolvedValueOnce({
			...fsServerDetail,
			header_names: ['Authorization']
		});

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add headers' }).click();
		await page
			.getByPlaceholder('Authorization: Bearer sk-...')
			.last()
			.fill('Authorization: Bearer sk-real-secret');
		await page.getByRole('button', { name: 'Save headers' }).click();

		expect(mcpServers.setHeaders).toHaveBeenCalledWith('mcp-1', {
			Authorization: 'Bearer sk-real-secret'
		});
	});

	it('clears headers on a server via mcpServers.setHeaders', async () => {
		const authedServer: MCPServerDetail = { ...fsServerDetail, header_names: ['Authorization'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...fsServerSummary, header_names: ['Authorization'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(authedServer);
		vi.mocked(mcpServers.setHeaders).mockResolvedValueOnce({ ...fsServerDetail, header_names: [] });

		render(McpServersPage);
		await page.getByRole('button', { name: 'Edit headers' }).click();
		await page.getByRole('button', { name: 'Clear all' }).click();

		expect(mcpServers.setHeaders).toHaveBeenCalledWith('mcp-1', {});
	});

	it('reconnects a server via mcpServers.reconnect', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.reconnect).mockResolvedValueOnce(fsServerDetail);

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Reconnect' }).click();

		expect(mcpServers.reconnect).toHaveBeenCalledWith('mcp-1');
	});

	it('does not submit while name or url is blank', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
	});

	it('shows an error when the initial load fails', async () => {
		vi.mocked(mcpServers.list).mockRejectedValueOnce(new Error('Failed to load MCP servers'));

		render(McpServersPage);

		await expect.element(page.getByText('Failed to load MCP servers')).toBeInTheDocument();
	});

	it('shows an ApiError message when reconnecting a server fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.reconnect).mockRejectedValueOnce(
			new ApiError(502, 'Server refused connection')
		);

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Reconnect' }).click();

		await expect.element(page.getByText('Server refused connection')).toBeInTheDocument();
	});

	it('removes a server via mcpServers.remove', async () => {
		vi.mocked(mcpServers.list).mockResolvedValueOnce([fsServerSummary]).mockResolvedValueOnce([]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.remove).mockResolvedValueOnce(undefined);

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Remove' }).click();

		expect(mcpServers.remove).toHaveBeenCalledWith('mcp-1');
		await expect
			.element(page.getByText('No MCP servers registered yet — add one above.'))
			.toBeInTheDocument();
	});

	it('shows an ApiError message when removing a server fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.remove).mockRejectedValueOnce(new ApiError(409, 'Server still in use'));

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Remove' }).click();

		await expect.element(page.getByText('Server still in use')).toBeInTheDocument();
	});

	it('shows an ApiError message when registering a server fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);
		vi.mocked(mcpServers.create).mockRejectedValueOnce(
			new ApiError(422, 'url must be a streamable-http endpoint')
		);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Bad server');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('not-a-url');
		await page.getByRole('button', { name: 'Register server' }).click();

		await expect
			.element(page.getByText('url must be a streamable-http endpoint'))
			.toBeInTheDocument();
	});

	it('filters the server list by name via the search box', async () => {
		const dbServer: MCPServerDetail = {
			...fsServerDetail,
			id: 'mcp-2',
			name: 'Database tools',
			tools: []
		};
		vi.mocked(mcpServers.list).mockResolvedValue([
			fsServerSummary,
			{ ...fsServerSummary, id: 'mcp-2', name: 'Database tools' }
		]);
		vi.mocked(mcpServers.get).mockImplementation((id) =>
			Promise.resolve(id === 'mcp-2' ? dbServer : fsServerDetail)
		);

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		await expect.element(page.getByText('Database tools')).toBeInTheDocument();

		await page.getByPlaceholder('Search MCP servers…').fill('data');

		await expect.element(page.getByText('Database tools')).toBeInTheDocument();
		await expect.element(page.getByText('Filesystem tools')).not.toBeInTheDocument();
	});

	it('filters the server list by connection status', async () => {
		const disconnected: MCPServerDetail = { ...fsServerDetail, id: 'mcp-2', connected: false };
		vi.mocked(mcpServers.list).mockResolvedValue([
			fsServerSummary,
			{ ...fsServerSummary, id: 'mcp-2', connected: false }
		]);
		vi.mocked(mcpServers.get).mockImplementation((id) =>
			Promise.resolve(id === 'mcp-2' ? disconnected : fsServerDetail)
		);

		render(McpServersPage);
		// "connected" is a substring of "disconnected", so match exact text.
		await expect.element(page.getByText('connected', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('disconnected', { exact: true })).toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Status' }).selectOptions('disconnected');

		await expect.element(page.getByText('disconnected', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('connected', { exact: true })).not.toBeInTheDocument();
	});

	// --- stdio transport (#187) ---

	const localServerSummary: MCPServer = {
		id: 'mcp-3',
		name: 'Local filesystem',
		transport: 'stdio',
		url: null,
		header_names: [],
		command: 'npx',
		args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
		env_names: [],
		connected: true,
		last_connected_at: '2026-08-01T00:00:00Z'
	};

	const localServerDetail: MCPServerDetail = { ...localServerSummary, tools: [] };

	it('switches the form fields when the stdio transport is selected', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);

		await expect
			.element(page.getByPlaceholder('URL (streamable-http endpoint)'))
			.toBeInTheDocument();
		await expect.element(page.getByPlaceholder('Command (e.g. npx)')).not.toBeInTheDocument();

		await page.getByText('Stdio (local command)').click();

		await expect.element(page.getByPlaceholder('Command (e.g. npx)')).toBeInTheDocument();
		await expect
			.element(page.getByPlaceholder('URL (streamable-http endpoint)'))
			.not.toBeInTheDocument();
	});

	it('registers a stdio server with command, args, and env via mcpServers.create', async () => {
		vi.mocked(mcpServers.list)
			.mockResolvedValueOnce([])
			.mockResolvedValueOnce([localServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(localServerDetail);
		vi.mocked(mcpServers.create).mockResolvedValueOnce(localServerDetail);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Local filesystem');
		await page.getByText('Stdio (local command)').click();
		await page.getByPlaceholder('Command (e.g. npx)').fill('npx');
		await page
			.getByPlaceholder('/path/to/dir')
			.fill('-y\n@modelcontextprotocol/server-filesystem\n/tmp');
		await page.getByText('Advanced: env vars').click();
		await page.getByPlaceholder('API_KEY: sk-...').fill('API_KEY: sk-real-secret');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Local filesystem',
			transport: 'stdio',
			command: 'npx',
			args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
			env: { API_KEY: 'sk-real-secret' }
		});
		await expect.element(page.getByText('Local filesystem')).toBeInTheDocument();
	});

	it('does not submit a stdio registration while the command is blank', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Local filesystem');
		await page.getByText('Stdio (local command)').click();
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
	});

	it('shows a stdio server’s command/args and env var names', async () => {
		const withEnv: MCPServerDetail = { ...localServerDetail, env_names: ['API_KEY'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...localServerSummary, env_names: ['API_KEY'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(withEnv);

		render(McpServersPage);

		await expect
			.element(page.getByText('npx -y @modelcontextprotocol/server-filesystem /tmp'))
			.toBeInTheDocument();
		await expect.element(page.getByText('Env vars: API_KEY')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Edit env vars' })).toBeInTheDocument();
	});

	it('adds env vars to an existing stdio server via mcpServers.setEnv', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([localServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(localServerDetail);
		vi.mocked(mcpServers.setEnv).mockResolvedValueOnce({
			...localServerDetail,
			env_names: ['API_KEY']
		});

		render(McpServersPage);
		await expect.element(page.getByText('Local filesystem')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add env vars' }).click();
		await page.getByPlaceholder('API_KEY: sk-...').fill('API_KEY: sk-real-secret');
		await page.getByRole('button', { name: 'Save env vars' }).click();

		expect(mcpServers.setEnv).toHaveBeenCalledWith('mcp-3', { API_KEY: 'sk-real-secret' });
	});

	it('clears env vars on a stdio server via mcpServers.setEnv', async () => {
		const withEnv: MCPServerDetail = { ...localServerDetail, env_names: ['API_KEY'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...localServerSummary, env_names: ['API_KEY'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(withEnv);
		vi.mocked(mcpServers.setEnv).mockResolvedValueOnce({ ...localServerDetail, env_names: [] });

		render(McpServersPage);
		await page.getByRole('button', { name: 'Edit env vars' }).click();
		await page.getByRole('button', { name: 'Clear all' }).click();

		expect(mcpServers.setEnv).toHaveBeenCalledWith('mcp-3', {});
	});

	it('does not show an "Edit headers"/"Add headers" action for a stdio server', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([localServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(localServerDetail);

		render(McpServersPage);
		await expect.element(page.getByText('Local filesystem')).toBeInTheDocument();

		await expect.element(page.getByRole('button', { name: 'Add headers' })).not.toBeInTheDocument();
	});

	it('skips blank lines when parsing header lines', async () => {
		vi.mocked(mcpServers.list).mockResolvedValueOnce([]).mockResolvedValueOnce([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.create).mockResolvedValueOnce(fsServerDetail);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByText('Advanced: auth headers').click();
		await page
			.getByPlaceholder('Authorization: Bearer sk-...')
			.first()
			.fill('Authorization: Bearer sk-real-secret\n\nX-Custom: val');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Filesystem tools',
			transport: 'streamable-http',
			url: 'http://localhost:9001',
			headers: { Authorization: 'Bearer sk-real-secret', 'X-Custom': 'val' }
		});
	});

	it('shows an error and does not submit when a header line has no name', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Filesystem tools');
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByText('Advanced: auth headers').click();
		await page.getByPlaceholder('Authorization: Bearer sk-...').first().fill(': value');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing a name/)).toBeInTheDocument();
	});

	it('does not submit while the name is blank', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('URL (streamable-http endpoint)').fill('http://localhost:9001');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
	});

	it('shows an error and does not submit when a stdio env line has no colon', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Local filesystem');
		await page.getByText('Stdio (local command)').click();
		await page.getByPlaceholder('Command (e.g. npx)').fill('npx');
		await page.getByText('Advanced: env vars').click();
		await page.getByPlaceholder('API_KEY: sk-...').fill('not-a-env-line');
		await page.getByRole('button', { name: 'Register server' }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing ":"/)).toBeInTheDocument();
	});

	it('shows an ApiError message when registering a stdio server fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);
		vi.mocked(mcpServers.create).mockRejectedValueOnce(
			new ApiError(422, 'command is not permitted')
		);

		render(McpServersPage);
		await page.getByPlaceholder('Name (e.g. Filesystem tools)').fill('Local filesystem');
		await page.getByText('Stdio (local command)').click();
		await page.getByPlaceholder('Command (e.g. npx)').fill('npx');
		await page.getByRole('button', { name: 'Register server' }).click();

		await expect.element(page.getByText('command is not permitted')).toBeInTheDocument();
	});

	it('switches the form back to streamable-http after selecting stdio', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([]);

		render(McpServersPage);
		await page.getByText('Stdio (local command)').click();
		await expect.element(page.getByPlaceholder('Command (e.g. npx)')).toBeInTheDocument();

		await page.getByText('Streamable-HTTP', { exact: true }).click();

		await expect
			.element(page.getByPlaceholder('URL (streamable-http endpoint)'))
			.toBeInTheDocument();
		await expect.element(page.getByPlaceholder('Command (e.g. npx)')).not.toBeInTheDocument();
	});

	it('shows an error and cancels out of the header editor without saving', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add headers' }).click();
		await page.getByPlaceholder('Authorization: Bearer sk-...').last().fill('not-a-header-line');
		await page.getByRole('button', { name: 'Save headers' }).click();

		expect(mcpServers.setHeaders).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing ":"/)).toBeInTheDocument();

		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect
			.element(page.getByRole('button', { name: 'Save headers' }))
			.not.toBeInTheDocument();
	});

	it('shows an ApiError message when saving headers fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([fsServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(fsServerDetail);
		vi.mocked(mcpServers.setHeaders).mockRejectedValueOnce(new ApiError(500, 'Vault unreachable'));

		render(McpServersPage);
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add headers' }).click();
		await page
			.getByPlaceholder('Authorization: Bearer sk-...')
			.last()
			.fill('Authorization: Bearer sk-real-secret');
		await page.getByRole('button', { name: 'Save headers' }).click();

		await expect.element(page.getByText('Vault unreachable')).toBeInTheDocument();
	});

	it('shows an ApiError message when clearing headers fails', async () => {
		const authedServer: MCPServerDetail = { ...fsServerDetail, header_names: ['Authorization'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...fsServerSummary, header_names: ['Authorization'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(authedServer);
		vi.mocked(mcpServers.setHeaders).mockRejectedValueOnce(new ApiError(500, 'Vault unreachable'));

		render(McpServersPage);
		await page.getByRole('button', { name: 'Edit headers' }).click();
		await page.getByRole('button', { name: 'Clear all' }).click();

		await expect.element(page.getByText('Vault unreachable')).toBeInTheDocument();
	});

	it('shows an error and cancels out of the env editor without saving', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([localServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(localServerDetail);

		render(McpServersPage);
		await expect.element(page.getByText('Local filesystem')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add env vars' }).click();
		await page.getByPlaceholder('API_KEY: sk-...').fill('not-a-env-line');
		await page.getByRole('button', { name: 'Save env vars' }).click();

		expect(mcpServers.setEnv).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing ":"/)).toBeInTheDocument();

		await page.getByRole('button', { name: 'Cancel' }).click();

		await expect.element(page.getByPlaceholder('API_KEY: sk-...')).not.toBeInTheDocument();
	});

	it('shows an ApiError message when saving env vars fails', async () => {
		vi.mocked(mcpServers.list).mockResolvedValue([localServerSummary]);
		vi.mocked(mcpServers.get).mockResolvedValue(localServerDetail);
		vi.mocked(mcpServers.setEnv).mockRejectedValueOnce(new ApiError(500, 'Vault unreachable'));

		render(McpServersPage);
		await expect.element(page.getByText('Local filesystem')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Add env vars' }).click();
		await page.getByPlaceholder('API_KEY: sk-...').fill('API_KEY: sk-real-secret');
		await page.getByRole('button', { name: 'Save env vars' }).click();

		await expect.element(page.getByText('Vault unreachable')).toBeInTheDocument();
	});

	it('shows an ApiError message when clearing env vars fails', async () => {
		const withEnv: MCPServerDetail = { ...localServerDetail, env_names: ['API_KEY'] };
		vi.mocked(mcpServers.list).mockResolvedValue([
			{ ...localServerSummary, env_names: ['API_KEY'] }
		]);
		vi.mocked(mcpServers.get).mockResolvedValue(withEnv);
		vi.mocked(mcpServers.setEnv).mockRejectedValueOnce(new ApiError(500, 'Vault unreachable'));

		render(McpServersPage);
		await page.getByRole('button', { name: 'Edit env vars' }).click();
		await page.getByRole('button', { name: 'Clear all' }).click();

		await expect.element(page.getByText('Vault unreachable')).toBeInTheDocument();
	});
});
