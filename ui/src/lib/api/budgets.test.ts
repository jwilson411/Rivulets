// Node-environment tests for the budget-cap resource client (see
// teams.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { budgets } from './budgets';

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

describe('budgets', () => {
	it('list() GETs /budgets', async () => {
		const fetchMock = mockFetch([{ id: 'b1' }]);

		const result = await budgets.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/budgets');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'b1' }]);
	});

	it('create() POSTs the cap definition to /budgets', async () => {
		const fetchMock = mockFetch({ id: 'b1' });

		await budgets.create({
			scope_type: 'workspace',
			period: 'month',
			limit_usd: 50,
			action: 'alert'
		});

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/budgets');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(
			JSON.stringify({
				scope_type: 'workspace',
				period: 'month',
				limit_usd: 50,
				action: 'alert'
			})
		);
	});

	it('update() PATCHes /budgets/:id with the patch', async () => {
		const fetchMock = mockFetch({ id: 'b1' });

		await budgets.update('b1', { limit_usd: 100, enabled: false });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/budgets/b1');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ limit_usd: 100, enabled: false }));
	});

	it('remove() DELETEs /budgets/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await budgets.remove('b1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/budgets/b1');
		expect(init.method).toBe('DELETE');
	});

	it('override() POSTs to /budgets/:id/override', async () => {
		const fetchMock = mockFetch({ id: 'b1', override_active: true });

		const result = await budgets.override('b1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/budgets/b1/override');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({}));
		expect(result).toEqual({ id: 'b1', override_active: true });
	});
});
