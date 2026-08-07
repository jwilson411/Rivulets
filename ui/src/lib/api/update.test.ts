// Node-environment tests for the update resource client (see agents.test.ts
// for the vi.mock('./auth.svelte') pattern).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { update } from './update';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('update', () => {
	it('status() GETs /update/status with the auth token', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await update.status();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/update/status');
		expect(init.method).toBe('GET');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});

	it('status() resolves with the parsed status payload', async () => {
		const payload = {
			current_version: '0.1.0',
			latest_version: 'v0.2.0',
			update_available: true,
			applicable: true
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
		);

		const result = await update.status();

		expect(result).toEqual(payload);
	});

	it('apply() POSTs to /update/apply', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(new Response(JSON.stringify({ status: 'restarting' }), { status: 202 }));
		vi.stubGlobal('fetch', fetchMock);

		const result = await update.apply();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/update/apply');
		expect(init.method).toBe('POST');
		expect(result).toEqual({ status: 'restarting' });
	});
});
