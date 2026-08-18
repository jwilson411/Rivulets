// Node-environment tests for the channels resource client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { channels } from './channels';

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

describe('channels', () => {
	it('list() GETs /channels', async () => {
		const fetchMock = mockFetch([{ id: 'c1' }]);

		const result = await channels.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'c1' }]);
	});

	it('get() GETs /channels/:id', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		const result = await channels.get('c1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/channels/c1');
		expect(result).toEqual({ id: 'c1' });
	});

	it('create() POSTs { name, description } to /channels', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await channels.create('general', 'chit chat');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ name: 'general', description: 'chit chat' }));
	});

	it('create() omits description from the JSON body when not given', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await channels.create('general');

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		// JSON.stringify drops undefined-valued keys entirely.
		expect(init.body).toBe('{"name":"general"}');
	});

	it('create() POSTs team_id when given (#411)', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await channels.create('My Channel', undefined, 'team-1');

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.body).toBe(JSON.stringify({ name: 'My Channel', team_id: 'team-1' }));
	});

	it('update() PATCHes /channels/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await channels.update('c1', { name: 'renamed', team_id: null });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels/c1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'renamed', team_id: null }));
	});

	it('remove() DELETEs /channels/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await channels.remove('c1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels/c1');
		expect(init.method).toBe('DELETE');
	});

	it('unarchive() POSTs an empty body to /channels/:id/unarchive', async () => {
		const fetchMock = mockFetch({ id: 'c1', archived: false });

		const result = await channels.unarchive('c1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels/c1/unarchive');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({}));
		expect(result).toEqual({ id: 'c1', archived: false });
	});
});
