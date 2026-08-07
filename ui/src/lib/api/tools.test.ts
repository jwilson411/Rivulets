// Node-environment tests for the custom tool CRUD + versioning client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { tools } from './tools';

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

describe('tools', () => {
	it('list() GETs /tools', async () => {
		const fetchMock = mockFetch([{ id: 'x1' }]);

		const result = await tools.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'x1' }]);
	});

	it('get() GETs /tools/:id', async () => {
		const fetchMock = mockFetch({ id: 'x1' });

		const result = await tools.get('x1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/tools/x1');
		expect(result).toEqual({ id: 'x1' });
	});

	it('create() POSTs the input to /tools', async () => {
		const fetchMock = mockFetch({ id: 'x1' });
		const input = { name: 'My tool', description: 'd', mode: 'advanced' as const };

		await tools.create(input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('update() PATCHes /tools/:id with the body', async () => {
		const fetchMock = mockFetch({ id: 'x1' });

		await tools.update('x1', { name: 'Renamed' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools/x1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ name: 'Renamed' }));
	});

	it('remove() DELETEs /tools/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await tools.remove('x1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools/x1');
		expect(init.method).toBe('DELETE');
	});

	it('listVersions() GETs /tools/:id/versions', async () => {
		const fetchMock = mockFetch([{ version: 1 }]);

		const result = await tools.listVersions('x1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/tools/x1/versions');
		expect(result).toEqual([{ version: 1 }]);
	});

	it('saveVersion() POSTs { source_code } to /tools/:id/versions', async () => {
		const fetchMock = mockFetch({ version: 2 });

		await tools.saveVersion('x1', 'console.log(1)');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools/x1/versions');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ source_code: 'console.log(1)' }));
	});

	it('rollback() POSTs to /tools/:id/versions/:version/rollback with no body', async () => {
		const fetchMock = mockFetch({ id: 'x1' });

		await tools.rollback('x1', 2);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools/x1/versions/2/rollback');
		expect(init.method).toBe('POST');
		expect(init.body).toBeUndefined();
	});

	it('openEditor() POSTs to /tools/:id/open-editor and returns the path', async () => {
		const fetchMock = mockFetch({ path: '/tmp/x1.py' });

		const result = await tools.openEditor('x1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/tools/x1/open-editor');
		expect(init.method).toBe('POST');
		expect(result).toEqual({ path: '/tmp/x1.py' });
	});
});
