// Node-environment tests for the MCP server discovery client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { mcpServers } from './mcpServers';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

function mockFetch(body: unknown, status = 200) {
	// A 204 Response may not carry a body -- JSON.stringify(null) would
	// otherwise produce the string "null", and the Response constructor
	// rejects a non-null body on a null-body status code.
	const responseBody = body === null ? null : JSON.stringify(body);
	const fetchMock = vi.fn().mockResolvedValue(new Response(responseBody, { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('mcpServers', () => {
	it('list() GETs /mcp-servers', async () => {
		const fetchMock = mockFetch([{ id: 'm1' }]);

		const result = await mcpServers.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/mcp-servers');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'm1' }]);
	});

	it('get() GETs /mcp-servers/:id', async () => {
		const fetchMock = mockFetch({ id: 'm1', tools: [] });

		const result = await mcpServers.get('m1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/mcp-servers/m1');
		expect(result).toEqual({ id: 'm1', tools: [] });
	});

	it('create() POSTs the input to /mcp-servers', async () => {
		const fetchMock = mockFetch({ id: 'm1' });
		const input = { name: 'local', url: 'http://localhost:9000' };

		await mcpServers.create(input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/mcp-servers');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('setHeaders() PUTs headers to /mcp-servers/:id/headers', async () => {
		const fetchMock = mockFetch({ id: 'm1', header_names: ['Authorization'] });

		await mcpServers.setHeaders('m1', { Authorization: 'Bearer secret' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/mcp-servers/m1/headers');
		expect(init.method).toBe('PUT');
		expect(init.body).toBe(JSON.stringify({ headers: { Authorization: 'Bearer secret' } }));
	});

	it('reconnect() POSTs to /mcp-servers/:id/reconnect with no body', async () => {
		const fetchMock = mockFetch({ id: 'm1', connected: true });

		await mcpServers.reconnect('m1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/mcp-servers/m1/reconnect');
		expect(init.method).toBe('POST');
		// JSON.stringify(undefined) is the value `undefined`, not the string "undefined".
		expect(init.body).toBeUndefined();
	});

	it('remove() DELETEs /mcp-servers/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await mcpServers.remove('m1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/mcp-servers/m1');
		expect(init.method).toBe('DELETE');
	});
});
