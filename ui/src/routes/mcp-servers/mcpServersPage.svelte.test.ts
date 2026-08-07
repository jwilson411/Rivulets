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
		reconnect: vi.fn(),
		remove: vi.fn()
	}
}));

const fsServerSummary: MCPServer = {
	id: 'mcp-1',
	name: 'Filesystem tools',
	url: 'http://localhost:9001',
	connected: true,
	last_connected_at: '2026-08-01T00:00:00Z'
};

const fsServerDetail: MCPServerDetail = {
	...fsServerSummary,
	tools: [{ id: 't-1', name: 'read_file', description: 'Reads a file', mcp_tool_name: 'read_file' }]
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
		await expect.element(page.getByText('connected')).toBeInTheDocument();
		await expect.element(page.getByText('1 tool: read_file')).toBeInTheDocument();
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

		await expect.element(page.getByText('disconnected')).toBeInTheDocument();
		await expect.element(page.getByText(/tool:/)).not.toBeInTheDocument();
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
			url: 'http://localhost:9001'
		});
		await expect.element(page.getByPlaceholder('Name (e.g. Filesystem tools)')).toHaveValue('');
		await expect.element(page.getByText('Filesystem tools')).toBeInTheDocument();
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
});
