// Node-environment tests for the workspace settings client (see
// agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { settings } from './settings';

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

describe('settings', () => {
	it('get() GETs /settings', async () => {
		const fetchMock = mockFetch({ 'ui.port': 5173 });

		const result = await settings.get();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/settings');
		expect(init.method).toBe('GET');
		expect(result).toEqual({ 'ui.port': 5173 });
	});

	it('update() PATCHes /settings with only the provided partial keys', async () => {
		const fetchMock = mockFetch({ 'guard.turn_limit': 20 });

		await settings.update({ 'guard.turn_limit': 20 });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/settings');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ 'guard.turn_limit': 20 }));
	});
});
