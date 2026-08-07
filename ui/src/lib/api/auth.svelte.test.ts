// Browser-mode test (vite.config.ts's "client" vitest project) -- auth's
// $state needs to be evaluated by Svelte's compiler, and `auth` is a module
// singleton like theme.svelte.ts. Each test logs in fresh (rather than
// relying on `it` execution order) so it doesn't depend on state left over
// from a previous test.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { auth } from './auth.svelte';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('auth', () => {
	it('login() posts the mnemonic as `key` (plus an optional passphrase) and stores the token', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(
				new Response(JSON.stringify({ token: 'tok-1', expires_at: '2099-01-01' }), { status: 200 })
			);
		vi.stubGlobal('fetch', fetchMock);

		await auth.login('apple banana cherry', 'secret');

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/auth/login');
		expect(init.body).toBe(JSON.stringify({ key: 'apple banana cherry', passphrase: 'secret' }));
		expect(auth.token).toBe('tok-1');
		expect(auth.isAuthenticated).toBe(true);
	});

	it('login() works without a passphrase', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(
				new Response(JSON.stringify({ token: 'tok-2', expires_at: 'x' }), { status: 200 })
			);
		vi.stubGlobal('fetch', fetchMock);

		await auth.login('apple banana cherry');

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.body).toBe(JSON.stringify({ key: 'apple banana cherry', passphrase: undefined }));
	});

	it('login() failure leaves the token exactly as it was before the call', async () => {
		// `auth` is a module singleton whose state carries across tests in
		// this file (like theme.svelte.test.ts), so this establishes its own
		// known baseline rather than assuming a fresh/null token.
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-x', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');
		expect(auth.token).toBe('tok-x');

		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('{"detail":"bad key"}', { status: 401 }))
		);

		await expect(auth.login('wrong words')).rejects.toThrow('bad key');
		expect(auth.token).toBe('tok-x');
		expect(auth.isAuthenticated).toBe(true);
	});

	it('logout() clears the token and posts to /auth/logout with the previous token as Bearer auth', async () => {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-3', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');

		const logoutFetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', logoutFetch);

		await auth.logout();

		expect(auth.token).toBeNull();
		expect(auth.isAuthenticated).toBe(false);
		const [url, init] = logoutFetch.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/auth/logout');
		expect((init.headers as Headers).get('Authorization')).toBe('Bearer tok-3');
	});

	it('applySession() sets token, humanId, displayName, and grant together', async () => {
		auth.applySession({
			token: 'tok-session',
			expires_at: 'x',
			human_id: 'human-1',
			display_name: 'Ada',
			grant: 'owner'
		});

		expect(auth.token).toBe('tok-session');
		expect(auth.humanId).toBe('human-1');
		expect(auth.displayName).toBe('Ada');
		expect(auth.grant).toBe('owner');
	});

	it('claimIdentity() with an existing humanId posts { human_id } and applies the session', async () => {
		auth.applySession({
			token: 'tok-before',
			expires_at: 'x',
			human_id: 'other',
			display_name: 'Other',
			grant: 'owner'
		});
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					token: 'tok-after',
					expires_at: 'x',
					human_id: 'human-1',
					display_name: 'Ada',
					grant: 'owner'
				}),
				{ status: 200 }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		await auth.claimIdentity({ humanId: 'human-1' });

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/auth/identity');
		expect(init.body).toBe(JSON.stringify({ human_id: 'human-1' }));
		expect(auth.humanId).toBe('human-1');
		expect(auth.displayName).toBe('Ada');
	});

	it('claimIdentity() with a new displayName posts { display_name }', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					token: 'tok-new',
					expires_at: 'x',
					human_id: 'human-2',
					display_name: 'Grace',
					grant: 'owner'
				}),
				{ status: 200 }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		await auth.claimIdentity({ displayName: 'Grace' });

		const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(init.body).toBe(JSON.stringify({ display_name: 'Grace' }));
		expect(auth.humanId).toBe('human-2');
	});

	it('clearIdentity() drops humanId/displayName without a fetch call, leaving the token intact', async () => {
		auth.applySession({
			token: 'tok-keep',
			expires_at: 'x',
			human_id: 'human-1',
			display_name: 'Ada',
			grant: 'owner'
		});

		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		auth.clearIdentity();

		expect(auth.humanId).toBeNull();
		expect(auth.displayName).toBeNull();
		expect(auth.token).toBe('tok-keep');
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('logout() is a no-op fetch-wise when already logged out', async () => {
		// Each stubGlobal below backs exactly one fetch call -- reusing a
		// single mockResolvedValue Response across login's and logout's
		// fetch calls would mean the second .json() read hits an
		// already-consumed body stream.
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-4', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');

		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
		await auth.logout();
		expect(auth.token).toBeNull();

		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		await auth.logout();

		expect(fetchMock).not.toHaveBeenCalled();
		expect(auth.token).toBeNull();
	});
});
