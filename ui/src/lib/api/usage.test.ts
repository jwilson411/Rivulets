// Node-environment tests for the usage resource client (see agents.test.ts
// for the vi.mock('./auth.svelte') pattern -- avoids needing browser mode
// just to read a static token value).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { usage } from './usage';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('usage', () => {
	it('defaults to the week range and includes the auth token', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await usage.get();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/usage?range=week');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});

	it('passes the requested range through as a query param', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await usage.get('month');

		const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/usage?range=month');
	});

	it('resolves with the parsed usage payload', async () => {
		const payload = {
			range: 'day',
			since: '2026-08-01T00:00:00Z',
			total_input_tokens: 100,
			total_output_tokens: 50,
			total_tokens: 150,
			total_cost_usd: 0.01,
			cost_incomplete: false,
			run_count: 3,
			by_agent: [],
			by_model: []
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
		);

		const result = await usage.get('day');

		expect(result).toEqual(payload);
	});
});
