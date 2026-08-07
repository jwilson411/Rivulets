// Node-environment tests for the humans resource client (see channels.test.ts
// for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { humans } from './humans';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

function mockFetch(body: unknown, status = 200) {
	const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('humans', () => {
	it('list() GETs /humans', async () => {
		const fetchMock = mockFetch([{ id: 'h1', display_name: 'Ada' }]);

		const result = await humans.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/humans');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'h1', display_name: 'Ada' }]);
	});
});
