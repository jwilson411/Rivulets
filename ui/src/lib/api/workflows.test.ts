// Node-environment tests for the workflows resource client (see
// agents.test.ts) -- verifies each function calls the right
// method/path/body against the shared `api` wrapper.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { workflows } from './workflows';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

function mockFetch(body: unknown, status = 200) {
	const responseBody = body === null ? null : JSON.stringify(body);
	const fetchMock = vi.fn().mockResolvedValue(new Response(responseBody, { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('workflows', () => {
	it('list() GETs /workflows', async () => {
		const fetchMock = mockFetch([{ id: 'w1' }]);

		const result = await workflows.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'w1' }]);
	});

	it('create() POSTs the input to /workflows', async () => {
		const fetchMock = mockFetch({ id: 'w1' });
		const input = { name: 'my-flow', description: 'does things' };

		await workflows.create(input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('update() PATCHes /workflows/:id', async () => {
		const fetchMock = mockFetch({ id: 'w1' });

		await workflows.update('w1', { name: 'renamed' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'renamed' }));
	});

	it('remove() DELETEs /workflows/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await workflows.remove('w1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1');
		expect(init.method).toBe('DELETE');
	});

	it('listNodes() GETs /workflows/:id/nodes', async () => {
		const fetchMock = mockFetch([{ id: 'n1' }]);

		const result = await workflows.listNodes('w1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/workflows/w1/nodes');
		expect(result).toEqual([{ id: 'n1' }]);
	});

	it('createNode() POSTs to /workflows/:id/nodes', async () => {
		const fetchMock = mockFetch({ id: 'n1' });
		const input = { name: 'Summarize', node_type: 'summarize' as const };

		await workflows.createNode('w1', input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1/nodes');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('updateNode() PATCHes /workflows/:id/nodes/:nodeId', async () => {
		const fetchMock = mockFetch({ id: 'n1' });

		await workflows.updateNode('w1', 'n1', { config: { template: '{input}!' } });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1/nodes/n1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ config: { template: '{input}!' } }));
	});

	it('removeNode() DELETEs /workflows/:id/nodes/:nodeId', async () => {
		const fetchMock = mockFetch(null, 204);

		await workflows.removeNode('w1', 'n1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1/nodes/n1');
		expect(init.method).toBe('DELETE');
	});

	it('listConnections() GETs /workflows/:id/connections', async () => {
		const fetchMock = mockFetch([{ id: 'c1' }]);

		const result = await workflows.listConnections('w1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/workflows/w1/connections');
		expect(result).toEqual([{ id: 'c1' }]);
	});

	it('createConnection() POSTs to /workflows/:id/connections', async () => {
		const fetchMock = mockFetch({ id: 'c1' });
		const input = { from_node_id: 'n1', to_node_id: 'n2' };

		await workflows.createConnection('w1', input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1/connections');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('removeConnection() DELETEs /workflows/:id/connections/:connectionId', async () => {
		const fetchMock = mockFetch(null, 204);

		await workflows.removeConnection('w1', 'c1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/workflows/w1/connections/c1');
		expect(init.method).toBe('DELETE');
	});

	it('listRuns() GETs /workflows/:id/runs', async () => {
		const fetchMock = mockFetch([{ id: 'r1' }]);

		const result = await workflows.listRuns('w1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/workflows/w1/runs');
		expect(result).toEqual([{ id: 'r1' }]);
	});

	it('listNodeRuns() GETs /workflows/:id/runs/:runId/node-runs', async () => {
		const fetchMock = mockFetch([{ id: 'nr1' }]);

		const result = await workflows.listNodeRuns('w1', 'r1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/workflows/w1/runs/r1/node-runs');
		expect(result).toEqual([{ id: 'nr1' }]);
	});

	it('sends the Authorization header derived from auth.token', async () => {
		const fetchMock = mockFetch([]);

		await workflows.list();

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});
});
