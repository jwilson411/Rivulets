// Node-environment tests for the agents resource client (vite.config.ts's
// "server" vitest project) -- verifies each function calls the right
// method/path/body against the shared `api` wrapper and returns the parsed
// response, the same way client.test.ts exercises `api` itself.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { agents } from './agents';

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

describe('agents', () => {
	it('list() GETs /agents and returns the parsed array', async () => {
		const fetchMock = mockFetch([{ id: 'a1' }]);

		const result = await agents.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/agents');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'a1' }]);
	});

	it('get() GETs /agents/:id', async () => {
		const fetchMock = mockFetch({ id: 'a1' });

		const result = await agents.get('a1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/agents/a1');
		expect(result).toEqual({ id: 'a1' });
	});

	it('create() POSTs the input to /agents', async () => {
		const fetchMock = mockFetch({ id: 'a1' });
		const input = { name: 'Bot', description: 'd', instructions: 'i', model: 'm' };

		await agents.create(input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/agents');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('update() PATCHes /agents/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 'a1' });

		await agents.update('a1', { name: 'New name' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/agents/a1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'New name' }));
	});

	it('remove() DELETEs /agents/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await agents.remove('a1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/agents/a1');
		expect(init.method).toBe('DELETE');
	});

	it('getRoutingRules() GETs /agents/:id/routing-rules', async () => {
		const fetchMock = mockFetch([{ id: 'r1' }]);

		const result = await agents.getRoutingRules('a1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/agents/a1/routing-rules');
		expect(result).toEqual([{ id: 'r1' }]);
	});

	it('setRoutingRules() PATCHes /agents/:id/routing-rules with { rules }', async () => {
		const fetchMock = mockFetch([{ id: 'r1' }]);
		const rules = [{ rule_type: 'keyword' as const, pattern: 'foo', priority: 1 }];

		await agents.setRoutingRules('a1', rules);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/agents/a1/routing-rules');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ rules }));
	});

	it('sends the Authorization header derived from auth.token', async () => {
		const fetchMock = mockFetch([]);

		await agents.list();

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});
});
