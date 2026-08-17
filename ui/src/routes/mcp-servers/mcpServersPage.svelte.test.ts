// Browser-mode component test for MCP servers (06-screens.md → MCP
// servers, mockup 2j): a list with a connected chip and an "Add an MCP
// server" sheet asking how it connects — "Web address" or "App on this
// machine", never transport slugs.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import McpServersPage from './+page.svelte';
import { mcpServers, type MCPServerDetail } from '$lib/api/mcpServers';

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

afterEach(() => {
	vi.clearAllMocks();
});

const filesystemServer: MCPServerDetail = {
	id: 'mcp-1',
	name: 'Filesystem tools',
	transport: 'streamable-http',
	url: 'http://localhost:9310/mcp',
	header_names: [],
	command: null,
	args: [],
	env_names: [],
	connected: true,
	last_connected_at: new Date().toISOString(),
	tools: [
		{
			id: 'mtool-1',
			name: 'read_file',
			description: 'Reads a file',
			mcp_tool_name: 'read_file',
			input_schema: { type: 'object' }
		}
	]
};

function seed(servers: MCPServerDetail[] = [filesystemServer]) {
	vi.mocked(mcpServers.list).mockResolvedValue(servers);
	vi.mocked(mcpServers.get).mockImplementation(async (id: string) =>
		servers.find((s) => s.id === id)!
	);
}

describe('mcp-servers/+page.svelte', () => {
	it('lists servers with a Connected chip and discovered tool count', async () => {
		seed();

		render(McpServersPage);

		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
		await expect.element(page.getByText('Connected')).toBeInTheDocument();
		await expect.element(page.getByText('1 tool discovered')).toBeInTheDocument();
	});

	it('says Not connected for a saved-but-failed server', async () => {
		seed([{ ...filesystemServer, connected: false, tools: [] }]);

		render(McpServersPage);

		await expect.element(page.getByText('Not connected')).toBeInTheDocument();
	});

	it('adds a web-address server from the sheet', async () => {
		seed([]);
		vi.mocked(mcpServers.create).mockResolvedValueOnce(filesystemServer);

		render(McpServersPage);
		await page.getByRole('button', { name: 'Add an MCP server' }).click();

		await page.getByLabelText('Name').fill('Filesystem tools');
		await page.getByLabelText('Address').fill('http://localhost:9310/mcp');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Filesystem tools',
			transport: 'streamable-http',
			url: 'http://localhost:9310/mcp',
			headers: undefined
		});
	});

	it('adds an app-on-this-machine server with command and one-arg-per-line', async () => {
		seed([]);
		vi.mocked(mcpServers.create).mockResolvedValueOnce({
			...filesystemServer,
			transport: 'stdio',
			url: null,
			command: 'npx'
		});

		render(McpServersPage);
		await page.getByRole('button', { name: 'Add an MCP server' }).click();
		await page.getByRole('button', { name: 'App on this machine' }).click();

		await page.getByLabelText('Name').fill('Filesystem tools');
		await page.getByLabelText('Command').fill('npx');
		await page
			.getByLabelText('Arguments — one per line')
			.fill('-y\n@modelcontextprotocol/server-filesystem');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(mcpServers.create).toHaveBeenCalledWith({
			name: 'Filesystem tools',
			transport: 'stdio',
			command: 'npx',
			args: ['-y', '@modelcontextprotocol/server-filesystem'],
			env: undefined
		});
	});

	it('rejects malformed header lines with a message instead of submitting', async () => {
		seed([]);

		render(McpServersPage);
		await page.getByRole('button', { name: 'Add an MCP server' }).click();
		await page.getByLabelText('Name').fill('Filesystem tools');
		await page.getByLabelText('Address').fill('http://localhost:9310/mcp');
		await page.getByText('More options').click();
		await page.getByLabelText('Auth headers').fill('not-a-header-line');
		await page.getByRole('button', { name: 'Connect', exact: true }).click();

		expect(mcpServers.create).not.toHaveBeenCalled();
		await expect.element(page.getByText(/missing ":"/)).toBeInTheDocument();
	});

	it('opens a server sheet with its tools and reconnects from it', async () => {
		seed();
		vi.mocked(mcpServers.reconnect).mockResolvedValueOnce(filesystemServer);

		render(McpServersPage);
		await page.getByRole('button', { name: /Filesystem tools/ }).click();

		await expect.element(page.getByText('read_file')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Reconnect' }).click();

		expect(mcpServers.reconnect).toHaveBeenCalledWith('mcp-1');
	});

	it('replaces the header set from the sheet (owner only)', async () => {
		seed();
		vi.mocked(mcpServers.setHeaders).mockResolvedValueOnce(filesystemServer);

		render(McpServersPage);
		await page.getByRole('button', { name: /Filesystem tools/ }).click();

		await page.getByLabelText('Auth headers').fill('Authorization: Bearer sk-123');
		await page.getByRole('button', { name: 'Save', exact: true }).click();

		expect(mcpServers.setHeaders).toHaveBeenCalledWith('mcp-1', {
			Authorization: 'Bearer sk-123'
		});
	});

	it('removes a server behind a confirm sheet', async () => {
		seed();
		vi.mocked(mcpServers.remove).mockResolvedValueOnce(undefined);

		render(McpServersPage);
		await page.getByRole('button', { name: /Filesystem tools/ }).click();
		await page.getByRole('button', { name: 'Remove server' }).click();

		expect(mcpServers.remove).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Remove server' }).click();

		expect(mcpServers.remove).toHaveBeenCalledWith('mcp-1');
	});

	it('shows a quiet error with retry when servers fail to load', async () => {
		vi.mocked(mcpServers.list).mockRejectedValue(new Error('boom'));

		render(McpServersPage);

		await expect.element(page.getByText("Couldn't load MCP servers.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
