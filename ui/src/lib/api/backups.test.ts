// Node-environment tests for the backups resource client (see agents.test.ts
// for the vi.mock('./auth.svelte') pattern).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { backups } from './backups';

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token' }
}));

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('backups', () => {
	it('list() GETs /backups with the auth token', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }));
		vi.stubGlobal('fetch', fetchMock);

		await backups.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/backups');
		expect(init.method).toBe('GET');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
	});

	it('list() resolves with the parsed backup list', async () => {
		const payload = [
			{ filename: 'manual-2026-01-01T000000Z.tar', kind: 'manual', size_bytes: 42, created_at: 'x' }
		];
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }))
		);

		const result = await backups.list();

		expect(result).toEqual(payload);
	});

	it('create() POSTs to /backups', async () => {
		const payload = {
			filename: 'manual-2026-01-01T000000Z.tar',
			kind: 'manual',
			size_bytes: 42,
			created_at: 'x'
		};
		const fetchMock = vi
			.fn()
			.mockResolvedValue(new Response(JSON.stringify(payload), { status: 201 }));
		vi.stubGlobal('fetch', fetchMock);

		const result = await backups.create();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/backups');
		expect(init.method).toBe('POST');
		expect(result).toEqual(payload);
	});

	it('restore() POSTs the filename back as confirm_filename', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetchMock);

		await backups.restore('manual-2026-01-01T000000Z.tar');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/backups/manual-2026-01-01T000000Z.tar/restore');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string)).toEqual({
			confirm_filename: 'manual-2026-01-01T000000Z.tar'
		});
	});
});
