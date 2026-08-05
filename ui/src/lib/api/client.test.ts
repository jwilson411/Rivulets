// Node-environment tests for the shared fetch wrapper (vite.config.ts's
// "server" vitest project) -- pure request/response logic, no DOM or
// component rendering needed, so this doesn't need the browser project.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './client';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('api client', () => {
	it('parses a successful JSON response', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
		);

		const result = await api.get('/whatever');

		expect(result).toEqual({ ok: true });
	});

	it('returns undefined for a 204 response', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

		const result = await api.delete('/whatever');

		expect(result).toBeUndefined();
	});

	it('throws ApiError with the parsed detail message on a FastAPI-shaped error', async () => {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ detail: 'name must be 3-80 chars' }), { status: 400 })
				)
		);

		await expect(api.post('/channels', { name: 'ab' })).rejects.toMatchObject({
			status: 400,
			message: 'name must be 3-80 chars'
		});
	});

	it('falls back to the raw response body when it is not JSON', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad Gateway', { status: 502 })));

		await expect(api.get('/whatever')).rejects.toBeInstanceOf(ApiError);
	});

	it('throws ApiError with status and message set from a non-JSON error body', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad Gateway', { status: 502 })));

		await expect(api.get('/whatever')).rejects.toMatchObject({
			status: 502,
			message: 'Bad Gateway'
		});
	});

	it('sets the Authorization header only when a token is provided', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await api.get('/whatever', 'my-token');

		const init = fetchMock.mock.calls[0][1] as RequestInit;
		const headers = init.headers as Headers;
		expect(headers.get('Authorization')).toBe('Bearer my-token');
	});

	it('omits the Authorization header when no token is provided', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await api.get('/whatever');

		const init = fetchMock.mock.calls[0][1] as RequestInit;
		const headers = init.headers as Headers;
		expect(headers.has('Authorization')).toBe(false);
	});

	it('JSON-encodes the body and sets Content-Type, resolved against /api/v1', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await api.post('/channels', { name: 'general' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/channels');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ name: 'general' }));
		expect((init.headers as Headers).get('Content-Type')).toBe('application/json');
	});
});
