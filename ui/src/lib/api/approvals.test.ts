// Node-environment tests for the unified approval queue client (see
// budgets.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { approvals } from './approvals';

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

describe('approvals', () => {
	it('list() GETs /approvals', async () => {
		const fetchMock = mockFetch([{ id: 'a1' }]);

		const result = await approvals.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/approvals');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'a1' }]);
	});

	it('approve() POSTs to /approvals/{id}/approve', async () => {
		const fetchMock = mockFetch({ id: 'a1', status: 'approved' });

		const result = await approvals.approve('a1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/approvals/a1/approve');
		expect(init.method).toBe('POST');
		expect(result).toEqual({ id: 'a1', status: 'approved' });
	});

	it('reject() POSTs to /approvals/{id}/reject', async () => {
		const fetchMock = mockFetch({ id: 'a1', status: 'rejected' });

		const result = await approvals.reject('a1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/approvals/a1/reject');
		expect(init.method).toBe('POST');
		expect(result).toEqual({ id: 'a1', status: 'rejected' });
	});
});
