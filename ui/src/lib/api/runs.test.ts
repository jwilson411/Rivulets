// Node-environment tests for the runs resource client (see usage.test.ts
// for the vi.mock('./auth.svelte') pattern -- avoids needing browser mode
// just to read a static token value).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { runs } from './runs';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('runs', () => {
	it('lists runs with no query param by default', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await runs.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/runs');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});

	it('passes a limit through as a query param', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await runs.list(10);

		const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/runs?limit=10');
	});

	it('fetches one trace by id', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await runs.get('trace-1');

		const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/runs/trace-1');
	});

	it('resolves with the parsed trace detail payload', async () => {
		const payload = {
			id: 'trace-1',
			trigger_type: 'message',
			label: 'hi',
			rivulet_id: null,
			channel_id: null,
			status: 'completed',
			span_count: 1,
			total_cost_usd: null,
			total_tokens: 0,
			started_at: '2026-08-09T06:00:00Z',
			completed_at: '2026-08-09T06:00:01Z',
			spans: []
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
		);

		const result = await runs.get('trace-1');

		expect(result).toEqual(payload);
	});
});
