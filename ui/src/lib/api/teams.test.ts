// Node-environment tests for the team resource client (see agents.test.ts
// for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { teams } from './teams';

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

describe('teams', () => {
	it('list() GETs /teams', async () => {
		const fetchMock = mockFetch([{ id: 't1' }]);

		const result = await teams.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/teams');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 't1' }]);
	});

	it('get() GETs /teams/:id', async () => {
		const fetchMock = mockFetch({ id: 't1', agent_ids: [] });

		const result = await teams.get('t1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/teams/t1');
		expect(result).toEqual({ id: 't1', agent_ids: [] });
	});

	it('create() POSTs { name, description } to /teams', async () => {
		const fetchMock = mockFetch({ id: 't1' });

		await teams.create('Squad', 'the squad');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/teams');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ name: 'Squad', description: 'the squad' }));
	});

	it('update() PATCHes /teams/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 't1' });

		await teams.update('t1', { agent_ids: ['a1', 'a2'] });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/teams/t1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ agent_ids: ['a1', 'a2'] }));
	});

	it('remove() DELETEs /teams/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await teams.remove('t1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/teams/t1');
		expect(init.method).toBe('DELETE');
	});
});
