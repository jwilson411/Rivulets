// Node-environment tests for the LLM provider config client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { providers } from './providers';

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

describe('providers', () => {
	it('list() GETs /providers', async () => {
		const fetchMock = mockFetch([{ id: 'p1' }]);

		const result = await providers.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'p1' }]);
	});

	it('get() GETs /providers/:id', async () => {
		const fetchMock = mockFetch({ id: 'p1' });

		const result = await providers.get('p1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers/p1');
		expect(init.method).toBe('GET');
		expect(result).toEqual({ id: 'p1' });
	});

	it('create() POSTs the input to /providers', async () => {
		const fetchMock = mockFetch({ id: 'p1' });
		const input = { provider: 'anthropic' as const, label: 'Prod key', api_key: 'sk-secret' };

		await providers.create(input);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify(input));
	});

	it('update() PATCHes /providers/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 'p1' });

		await providers.update('p1', { label: 'Renamed' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers/p1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ label: 'Renamed' }));
	});

	it('remove() DELETEs /providers/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await providers.remove('p1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers/p1');
		expect(init.method).toBe('DELETE');
	});

	it('credentialStorage() GETs /providers/credential-storage', async () => {
		const fetchMock = mockFetch({ backend: 'fallback' });

		const result = await providers.credentialStorage();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/providers/credential-storage');
		expect(init.method).toBe('GET');
		expect(result).toEqual({ backend: 'fallback' });
	});
});
