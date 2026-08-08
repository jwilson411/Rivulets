// Node-environment tests for the invites resource client (see
// channels.test.ts for the shared pattern this follows).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { invites } from './invites';

const applySessionMock = vi.hoisted(() => vi.fn());

vi.mock('./auth.svelte', () => ({
	auth: { token: 'test-token', applySession: applySessionMock }
}));

afterEach(() => {
	vi.unstubAllGlobals();
	applySessionMock.mockClear();
});

function mockFetch(body: unknown, status = 200) {
	const responseBody = body === null ? null : JSON.stringify(body);
	const fetchMock = vi.fn().mockResolvedValue(new Response(responseBody, { status }));
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

describe('invites', () => {
	it('list() GETs /invites', async () => {
		const fetchMock = mockFetch([{ id: 'i1' }]);

		const result = await invites.list();

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/invites');
		expect(init.method).toBe('GET');
		expect(result).toEqual([{ id: 'i1' }]);
	});

	it('create() POSTs the invite params to /invites, mapping to snake_case', async () => {
		const fetchMock = mockFetch({ invite_id: 'i1', url: 'http://x/invite/i1.secret' });

		await invites.create({ displayNameHint: 'Ada', maxUses: 2, expiresInHours: 24 });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/invites');
		expect(init.method).toBe('POST');
		expect(init.body).toBe(
			JSON.stringify({ display_name_hint: 'Ada', max_uses: 2, expires_in_hours: 24 })
		);
	});

	it('revoke() DELETEs /invites/:id', async () => {
		const fetchMock = mockFetch(null, 204);

		await invites.revoke('i1');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/invites/i1');
		expect(init.method).toBe('DELETE');
	});

	it('accept() POSTs the invite token without a bearer token and applies the returned session', async () => {
		const fetchMock = mockFetch({
			token: 'tok-1',
			expires_at: 'x',
			human_id: 'human-1',
			display_name: 'Ada',
			grant: 'invite'
		});

		await invites.accept('inv-1.secret', 'Ada');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/invites/accept');
		expect(init.body).toBe(JSON.stringify({ invite_token: 'inv-1.secret', display_name: 'Ada' }));
		expect((init.headers as Headers).has('Authorization')).toBe(false);
		expect(applySessionMock).toHaveBeenCalledWith({
			token: 'tok-1',
			expires_at: 'x',
			human_id: 'human-1',
			display_name: 'Ada',
			grant: 'invite'
		});
	});

	it('accept() works without a display name', async () => {
		const fetchMock = mockFetch({
			token: 'tok-2',
			expires_at: 'x',
			human_id: 'human-2',
			display_name: 'Guest',
			grant: 'invite'
		});

		await invites.accept('inv-1.secret');

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.body).toBe(
			JSON.stringify({ invite_token: 'inv-1.secret', display_name: undefined })
		);
	});
});
