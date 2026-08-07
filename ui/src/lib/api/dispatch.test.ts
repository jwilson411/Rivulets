// Node-environment tests for the dispatch resource client (see agents.test.ts
// for the vi.mock('./auth.svelte') pattern -- avoids needing browser mode
// just to read a static token value).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { dispatch } from './dispatch';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('dispatch', () => {
	it('defaults to the week range and includes the auth token', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await dispatch.hitRate();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/dispatch/hit-rate?range=week');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});

	it('passes the requested range through as a query param', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await dispatch.hitRate('day');

		const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/dispatch/hit-rate?range=day');
	});

	it('resolves with the parsed hit-rate payload', async () => {
		const payload = {
			range: 'week',
			since: '2026-08-01T00:00:00Z',
			total_decisions: 10,
			hit_count: 9,
			fallback_count: 1,
			hit_rate: 0.9,
			fallback_rate: 0.1,
			fallback_warning: false,
			by_method: [
				{ method: 'deterministic', count: 9 },
				{ method: 'llm', count: 1 }
			]
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
		);

		const result = await dispatch.hitRate('week');

		expect(result).toEqual(payload);
	});
});
