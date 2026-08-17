// Browser-mode test (vite.config.ts's "client" vitest project) -- auth's
// $state needs to be evaluated by Svelte's compiler, and `auth` is a module
// singleton like theme.svelte.ts. Each test logs in fresh (rather than
// relying on `it` execution order) so it doesn't depend on state left over
// from a previous test.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { auth } from './auth.svelte';

const RESUME_STORAGE_KEY = 'rivulets-invite-resume';
const OWNER_STAY_STORAGE_KEY = 'rivulets-owner-stay';

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
	localStorage.removeItem(RESUME_STORAGE_KEY);
	auth.forgetOwnerStay();
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

	it('login() stores expires_at', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(JSON.stringify({ token: 'tok-5', expires_at: '2099-06-01T00:00:00Z' }), {
					status: 200
				})
			)
		);

		await auth.login('a b c');

		expect(auth.expiresAt).toBe('2099-06-01T00:00:00Z');
	});

	it('clears the session and flips on sessionExpired when the JWT expiry timer fires', async () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-01T00:00:00Z'));
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(JSON.stringify({ token: 'tok-6', expires_at: '2024-01-01T00:00:05Z' }), {
					status: 200
				})
			)
		);

		await auth.login('a b c');
		expect(auth.isAuthenticated).toBe(true);
		expect(auth.sessionExpired).toBe(false);

		await vi.advanceTimersByTimeAsync(5000);

		expect(auth.token).toBeNull();
		expect(auth.isAuthenticated).toBe(false);
		expect(auth.sessionExpired).toBe(true);
	});

	it('a 401 on an authenticated request clears the session and sets sessionExpired', async () => {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-7', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');
		expect(auth.isAuthenticated).toBe(true);

		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('{"detail":"token expired"}', { status: 401 }))
		);

		await expect(auth.claimIdentity({ displayName: 'Ada' })).rejects.toThrow('token expired');

		expect(auth.token).toBeNull();
		expect(auth.isAuthenticated).toBe(false);
		expect(auth.sessionExpired).toBe(true);
	});

	it('a 401 on the login attempt itself does not touch sessionExpired', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
		await auth.logout();
		expect(auth.sessionExpired).toBe(false);

		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('{"detail":"bad key"}', { status: 401 }))
		);

		await expect(auth.login('wrong words')).rejects.toThrow('bad key');

		expect(auth.sessionExpired).toBe(false);
	});

	it('rememberInviteSession() applies the session and persists the resume credential (#350)', async () => {
		auth.rememberInviteSession({
			token: 'tok-invite',
			expires_at: 'x',
			human_id: 'human-9',
			display_name: 'Ada',
			grant: 'invite',
			resume_token: 'sess-1.resume-secret'
		});

		expect(auth.token).toBe('tok-invite');
		expect(auth.grant).toBe('invite');
		expect(auth.resumeDisplayName).toBe('Ada');
		expect(JSON.parse(localStorage.getItem(RESUME_STORAGE_KEY)!)).toEqual({
			token: 'sess-1.resume-secret',
			displayName: 'Ada'
		});
	});

	it('resumeInviteSession() exchanges the stored credential for a fresh session', async () => {
		localStorage.setItem(
			RESUME_STORAGE_KEY,
			JSON.stringify({ token: 'sess-1.resume-secret', displayName: 'Ada' })
		);
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					token: 'tok-resumed',
					expires_at: 'x',
					human_id: 'human-9',
					display_name: 'Ada',
					grant: 'invite',
					resume_token: 'sess-1.resume-secret'
				}),
				{ status: 200 }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		await expect(auth.resumeInviteSession()).resolves.toBe(true);

		const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('/api/v1/invites/resume');
		expect(init.body).toBe(JSON.stringify({ resume_token: 'sess-1.resume-secret' }));
		expect(auth.token).toBe('tok-resumed');
		expect(auth.grant).toBe('invite');
		expect(auth.humanId).toBe('human-9');
	});

	it('resumeInviteSession() resolves false without a request when nothing is stored', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		await expect(auth.resumeInviteSession()).resolves.toBe(false);

		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('resumeInviteSession() discards a credential the server rejects as dead (403)', async () => {
		auth.rememberInviteSession({
			token: 'tok-invite',
			expires_at: 'x',
			human_id: 'human-9',
			display_name: 'Ada',
			grant: 'invite',
			resume_token: 'sess-1.resume-secret'
		});
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response('{"detail":"This invite has been revoked"}', { status: 403 })
				)
		);

		await expect(auth.resumeInviteSession()).resolves.toBe(false);

		expect(localStorage.getItem(RESUME_STORAGE_KEY)).toBeNull();
		expect(auth.resumeDisplayName).toBeNull();
	});

	it('resumeInviteSession() keeps the credential and rethrows on a transient failure (503)', async () => {
		localStorage.setItem(
			RESUME_STORAGE_KEY,
			JSON.stringify({ token: 'sess-1.resume-secret', displayName: 'Ada' })
		);
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response('{"detail":"This workspace is not unlocked"}', { status: 503 })
				)
		);

		await expect(auth.resumeInviteSession()).rejects.toThrow('not unlocked');

		expect(JSON.parse(localStorage.getItem(RESUME_STORAGE_KEY)!).token).toBe(
			'sess-1.resume-secret'
		);
	});

	it('logout() leaves the persisted resume credential in place (#350)', async () => {
		auth.rememberInviteSession({
			token: 'tok-invite',
			expires_at: 'x',
			human_id: 'human-9',
			display_name: 'Ada',
			grant: 'invite',
			resume_token: 'sess-1.resume-secret'
		});
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

		await auth.logout();

		expect(auth.token).toBeNull();
		expect(auth.resumeDisplayName).toBe('Ada');
		expect(JSON.parse(localStorage.getItem(RESUME_STORAGE_KEY)!).token).toBe(
			'sess-1.resume-secret'
		);
	});

	it('rememberOwnerStay() persists the phrase so a later resume can re-derive (#407)', async () => {
		auth.rememberOwnerStay('apple banana cherry', 'secret');

		expect(auth.ownerStayEnabled).toBe(true);
		expect(JSON.parse(localStorage.getItem(OWNER_STAY_STORAGE_KEY)!)).toEqual({
			mnemonic: 'apple banana cherry',
			passphrase: 'secret'
		});
	});

	it('resumeOwnerSession() logs in with the stored phrase and re-claims the last identity', async () => {
		localStorage.setItem(
			OWNER_STAY_STORAGE_KEY,
			JSON.stringify({
				mnemonic: 'apple banana cherry',
				passphrase: 'secret',
				humanId: 'human-1'
			})
		);
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ token: 'tok-stay', expires_at: 'x', grant: 'owner' }), {
					status: 200
				})
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						token: 'tok-claimed',
						expires_at: 'x',
						human_id: 'human-1',
						display_name: 'Ada',
						grant: 'owner'
					}),
					{ status: 200 }
				)
			);
		vi.stubGlobal('fetch', fetchMock);

		await expect(auth.resumeOwnerSession()).resolves.toBe(true);

		expect(fetchMock).toHaveBeenCalledTimes(2);
		const [loginUrl, loginInit] = fetchMock.mock.calls[0] as [string, RequestInit];
		expect(loginUrl).toBe('/api/v1/auth/login');
		expect(loginInit.body).toBe(
			JSON.stringify({ key: 'apple banana cherry', passphrase: 'secret' })
		);
		const [identityUrl, identityInit] = fetchMock.mock.calls[1] as [string, RequestInit];
		expect(identityUrl).toBe('/api/v1/auth/identity');
		expect(identityInit.body).toBe(JSON.stringify({ human_id: 'human-1' }));
		expect(auth.token).toBe('tok-claimed');
		expect(auth.humanId).toBe('human-1');
		expect(auth.ownerStayEnabled).toBe(true);
	});

	it('resumeOwnerSession() resolves false without a request when nothing is stored', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		await expect(auth.resumeOwnerSession()).resolves.toBe(false);

		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('resumeOwnerSession() discards a credential the server rejects (401)', async () => {
		auth.rememberOwnerStay('apple banana cherry');
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(new Response('{"detail":"Incorrect recovery phrase"}', { status: 401 }))
		);

		await expect(auth.resumeOwnerSession()).resolves.toBe(false);

		expect(localStorage.getItem(OWNER_STAY_STORAGE_KEY)).toBeNull();
		expect(auth.ownerStayEnabled).toBe(false);
	});

	it('resumeOwnerSession() keeps the credential and rethrows on a transient failure (503)', async () => {
		auth.rememberOwnerStay('apple banana cherry');
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response('{"detail":"This workspace is not unlocked"}', { status: 503 })
				)
		);

		await expect(auth.resumeOwnerSession()).rejects.toThrow('not unlocked');

		expect(JSON.parse(localStorage.getItem(OWNER_STAY_STORAGE_KEY)!).mnemonic).toBe(
			'apple banana cherry'
		);
		expect(auth.ownerStayEnabled).toBe(true);
	});

	it('resumeOwnerSession() still signs in when the stored identity can no longer be claimed', async () => {
		localStorage.setItem(
			OWNER_STAY_STORAGE_KEY,
			JSON.stringify({ mnemonic: 'apple banana cherry', humanId: 'gone' })
		);
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ token: 'tok-stay', expires_at: 'x', grant: 'owner' }), {
					status: 200
				})
			)
			.mockResolvedValueOnce(new Response('{"detail":"Unknown human"}', { status: 404 }));
		vi.stubGlobal('fetch', fetchMock);

		await expect(auth.resumeOwnerSession()).resolves.toBe(true);

		expect(auth.token).toBe('tok-stay');
		expect(auth.humanId).toBeNull();
		expect(auth.ownerStayEnabled).toBe(true);
	});

	it('claimIdentity() records the claimed human on a stored stay credential', async () => {
		auth.rememberOwnerStay('apple banana cherry');
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					token: 'tok-claimed',
					expires_at: 'x',
					human_id: 'human-1',
					display_name: 'Ada',
					grant: 'owner'
				}),
				{ status: 200 }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		await auth.claimIdentity({ displayName: 'Ada' });

		expect(JSON.parse(localStorage.getItem(OWNER_STAY_STORAGE_KEY)!)).toEqual({
			mnemonic: 'apple banana cherry',
			humanId: 'human-1'
		});
	});

	it('logout() drops the owner stay credential (#407)', async () => {
		auth.rememberOwnerStay('apple banana cherry');
		auth.applySession({
			token: 'tok-owner',
			expires_at: 'x',
			human_id: 'human-1',
			display_name: 'Ada',
			grant: 'owner'
		});
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

		await auth.logout();

		expect(auth.token).toBeNull();
		expect(auth.ownerStayEnabled).toBe(false);
		expect(localStorage.getItem(OWNER_STAY_STORAGE_KEY)).toBeNull();
	});

	it('login() clears a stale sessionExpired flag on success', async () => {
		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-8', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');

		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('{"detail":"expired"}', { status: 401 }))
		);
		await expect(auth.claimIdentity({ displayName: 'Ada' })).rejects.toThrow();
		expect(auth.sessionExpired).toBe(true);

		vi.stubGlobal(
			'fetch',
			vi
				.fn()
				.mockResolvedValue(
					new Response(JSON.stringify({ token: 'tok-9', expires_at: 'x' }), { status: 200 })
				)
		);
		await auth.login('a b c');

		expect(auth.sessionExpired).toBe(false);
	});
});
