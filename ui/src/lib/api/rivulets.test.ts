// Node-environment tests for the rivulet & message resource client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { rivulets } from './rivulets';

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

describe('rivulets', () => {
	it('listForChannel() GETs /channels/:id/rivulets', async () => {
		const fetchMock = mockFetch([{ id: 'r1' }]);

		const result = await rivulets.listForChannel('c1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels/c1/rivulets');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'r1' }]);
	});

	it('create() POSTs { content, files } to /channels/:id/rivulets', async () => {
		const fetchMock = mockFetch({ id: 'r1' });

		await rivulets.create('c1', 'hello', ['f1', 'f2']);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels/c1/rivulets');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ content: 'hello', files: ['f1', 'f2'] }));
	});

	it('create() defaults files to an empty array', async () => {
		const fetchMock = mockFetch({ id: 'r1' });

		await rivulets.create('c1', 'hello');

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.body).toBe(JSON.stringify({ content: 'hello', files: [] }));
	});

	it('get() GETs /rivulets/:id', async () => {
		const fetchMock = mockFetch({ id: 'r1' });

		const result = await rivulets.get('r1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/rivulets/r1');
		expect(result).toEqual({ id: 'r1' });
	});

	it('listMessages() GETs /rivulets/:id/messages', async () => {
		const fetchMock = mockFetch([{ id: 'm1' }]);

		const result = await rivulets.listMessages('r1');

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/rivulets/r1/messages');
		expect(result).toEqual([{ id: 'm1' }]);
	});

	it('postMessage() POSTs { content, files } to /rivulets/:id/messages', async () => {
		const fetchMock = mockFetch({ id: 'm1' });

		await rivulets.postMessage('r1', 'hi there', ['f1']);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/rivulets/r1/messages');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ content: 'hi there', files: ['f1'] }));
	});

	it('resume() POSTs an empty body to /rivulets/:id/resume', async () => {
		const fetchMock = mockFetch({ id: 'r1', status: 'active' });

		await rivulets.resume('r1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/rivulets/r1/resume');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({}));
	});
});
