// Node-environment tests for the P2P sync status/control/conflicts client
// (see agents.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { sync } from './sync';

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

describe('sync', () => {
	it('status() GETs /sync/status', async () => {
		const fetchMock = mockFetch({ running: true, node_id: 'n1', peers: [], pending_changes: 0 });

		const result = await sync.status();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/status');
		expect(init.method).toBe('GET');
		expect(result).toEqual({ running: true, node_id: 'n1', peers: [], pending_changes: 0 });
	});

	it('connect() POSTs { address } to /sync/connect', async () => {
		const fetchMock = mockFetch({ peer_id: 'p1', address: '1.2.3.4', connected: true });

		await sync.connect('1.2.3.4');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/connect');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ address: '1.2.3.4' }));
	});

	it('disconnect() POSTs { peer_id } to /sync/disconnect', async () => {
		const fetchMock = mockFetch(null, 204);

		await sync.disconnect('p1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/disconnect');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ peer_id: 'p1' }));
	});

	it('conflicts() GETs /sync/conflicts', async () => {
		const fetchMock = mockFetch([{ id: 'c1' }]);

		const result = await sync.conflicts();

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/sync/conflicts');
		expect(result).toEqual([{ id: 'c1' }]);
	});

	it('resolveConflict() POSTs { keep } to /sync/conflicts/:id/resolve', async () => {
		const fetchMock = mockFetch({ id: 'c1' });

		await sync.resolveConflict('c1', 'remote');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/conflicts/c1/resolve');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({ keep: 'remote' }));
	});

	it('getCapabilities() GETs /sync/capabilities', async () => {
		const fetchMock = mockFetch({ capabilities: ['router'] });

		const result = await sync.getCapabilities();

		const [url] = fetchMock.mock.calls[0] as [string];
		expect(url).toBe('/api/v1/sync/capabilities');
		expect(result).toEqual({ capabilities: ['router'] });
	});

	it('coordinator() GETs /sync/coordinator', async () => {
		const fetchMock = mockFetch({
			running: true,
			node_id: 'n1',
			coordinator_id: 'n1',
			term: 3,
			is_self: true,
			self_score: 0.9,
			peer_scores: {}
		});

		const result = await sync.coordinator();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/coordinator');
		expect(init.method).toBe('GET');
		expect(result).toMatchObject({ coordinator_id: 'n1', term: 3, is_self: true });
	});

	it('reclaimCoordinator() POSTs to /sync/coordinator/reclaim', async () => {
		const fetchMock = mockFetch({
			running: true,
			node_id: 'n1',
			coordinator_id: 'n1',
			term: 4,
			is_self: true,
			self_score: 0.9,
			peer_scores: {}
		});

		const result = await sync.reclaimCoordinator();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/coordinator/reclaim');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(JSON.stringify({}));
		expect(result).toMatchObject({ term: 4 });
	});

	it('setCapabilities() PATCHes /sync/capabilities with { capabilities }', async () => {
		const fetchMock = mockFetch({ capabilities: ['router'] });

		await sync.setCapabilities(['router']);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/sync/capabilities');
		expect(init.method).toBe('PATCH');
		expect(init.body).toBe(JSON.stringify({ capabilities: ['router'] }));
	});
});
