// Reactive session state (api-design.md#authentication-flow). The JWT lives
// in memory only — never localStorage, and never sessionStorage except
// the one-shot OAuth hop (#464) — per security.md's "JWT in memory (not
// localStorage)" mitigation for DOM-based XSS. A refresh therefore cannot
// reuse the JWT; staying signed in means re-deriving a fresh one.
//
// #407: owners can opt in to "Stay signed in on this machine". That
// persists the recovery phrase (and optional passphrase) in localStorage
// so a refresh, new tab, or typed URL can POST /auth/login again and
// re-claim the last identity. The JWT itself still never touches storage
// except for the one-shot OAuth hop below. This is off by default and
// cleared on explicit sign-out — storing the phrase is a real XSS/local-
// access tradeoff, so LoginForm discloses it next to the checkbox rather
// than doing it silently.
//
// #464: "Connect Google account" is a same-tab trip off-origin. The JWT
// is memory-only, so coming back would dump the owner on Unlock unless
// stay-signed-in was already on. Parking the *current session* (never
// the recovery phrase) in sessionStorage for that hop lets the layout
// restore it without writing the phrase to localStorage. Tab-scoped,
// deleted on consume / sign-out / tab close.
//
// #350: invite-grant sessions are the other deliberate exception to
// "nothing in localStorage" — not for the JWT itself (still memory-only),
// but for the invite *resume token* (api/invites.py's POST
// /invites/resume). An invited human has no mnemonic to re-login with and
// can't re-redeem a spent single-use invite, so without a persisted
// re-entry credential a refresh or sign-out locks them out permanently.
// The stored token is scoped (can only ever mint grant="invite" sessions),
// revocable by the owner (revoking the invite kills it), idle-expiring
// server-side, and lower-value than what an invited browser already
// persists anyway — the original invite URL sitting in its history.

import { api, ApiError, onUnauthorized } from './client';

interface LoginResponse {
	token: string;
	expires_at: string;
	grant: string;
}

// The shape shared by POST /auth/identity and POST /invites/accept (#14/#15)
// -- both mint a session that's already claimed a Human identity, unlike
// plain login above, which leaves human_id unset until IdentityPicker calls
// one of those two endpoints.
export interface SessionInfo {
	token: string;
	expires_at: string;
	human_id: string;
	display_name: string;
	grant: string;
}

// POST /invites/accept and POST /invites/resume both return this (#350):
// a claimed-identity session plus the persistent re-entry credential.
export interface InviteSessionInfo extends SessionInfo {
	resume_token: string;
}

interface StreamTicketResponse {
	ticket: string;
	expires_at: string;
}

const RESUME_STORAGE_KEY = 'rivulets-invite-resume';
const OWNER_STAY_STORAGE_KEY = 'rivulets-owner-stay';
const OAUTH_HOP_STORAGE_KEY = 'rivulets-oauth-hop';

interface StoredResume {
	token: string;
	displayName: string;
}

interface StoredOwnerStay {
	mnemonic: string;
	passphrase?: string;
	humanId?: string;
}

// Same `typeof localStorage` guard as theme.svelte.ts's readStored — the
// UI never server-renders (+layout.ts's `ssr = false`), but node-env unit
// tests import this module without a DOM.
function readStoredResume(): StoredResume | null {
	if (typeof localStorage === 'undefined') return null;
	const raw = localStorage.getItem(RESUME_STORAGE_KEY);
	if (!raw) return null;
	try {
		const parsed: unknown = JSON.parse(raw);
		if (
			parsed &&
			typeof parsed === 'object' &&
			typeof (parsed as StoredResume).token === 'string' &&
			typeof (parsed as StoredResume).displayName === 'string'
		) {
			return parsed as StoredResume;
		}
	} catch {
		// fall through — a corrupt entry is treated as absent
	}
	return null;
}

function writeStoredResume(value: StoredResume | null): void {
	if (typeof localStorage === 'undefined') return;
	if (value === null) localStorage.removeItem(RESUME_STORAGE_KEY);
	else localStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify(value));
}

function readStoredOwnerStay(): StoredOwnerStay | null {
	if (typeof localStorage === 'undefined') return null;
	const raw = localStorage.getItem(OWNER_STAY_STORAGE_KEY);
	if (!raw) return null;
	try {
		const parsed: unknown = JSON.parse(raw);
		if (
			parsed &&
			typeof parsed === 'object' &&
			typeof (parsed as StoredOwnerStay).mnemonic === 'string' &&
			(parsed as StoredOwnerStay).mnemonic.length > 0
		) {
			const stay = parsed as StoredOwnerStay;
			return {
				mnemonic: stay.mnemonic,
				passphrase:
					typeof stay.passphrase === 'string' && stay.passphrase.length > 0
						? stay.passphrase
						: undefined,
				humanId:
					typeof stay.humanId === 'string' && stay.humanId.length > 0 ? stay.humanId : undefined
			};
		}
	} catch {
		// fall through — a corrupt entry is treated as absent
	}
	return null;
}

function writeStoredOwnerStay(value: StoredOwnerStay | null): void {
	if (typeof localStorage === 'undefined') return;
	if (value === null) localStorage.removeItem(OWNER_STAY_STORAGE_KEY);
	else localStorage.setItem(OWNER_STAY_STORAGE_KEY, JSON.stringify(value));
}

function isSessionInfo(parsed: unknown): parsed is SessionInfo {
	return (
		!!parsed &&
		typeof parsed === 'object' &&
		typeof (parsed as SessionInfo).token === 'string' &&
		(parsed as SessionInfo).token.length > 0 &&
		typeof (parsed as SessionInfo).expires_at === 'string' &&
		typeof (parsed as SessionInfo).grant === 'string' &&
		typeof (parsed as SessionInfo).human_id === 'string' &&
		typeof (parsed as SessionInfo).display_name === 'string'
	);
}

function writeParkedOAuthHop(value: SessionInfo | null): void {
	if (typeof sessionStorage === 'undefined') return;
	try {
		if (value === null) sessionStorage.removeItem(OAUTH_HOP_STORAGE_KEY);
		else sessionStorage.setItem(OAUTH_HOP_STORAGE_KEY, JSON.stringify(value));
	} catch {
		// Private mode / quota — connect still navigates; the owner may
		// have to Unlock if stay-signed-in is also off.
	}
}

function takeParkedOAuthHop(): SessionInfo | null {
	if (typeof sessionStorage === 'undefined') return null;
	try {
		const raw = sessionStorage.getItem(OAUTH_HOP_STORAGE_KEY);
		sessionStorage.removeItem(OAUTH_HOP_STORAGE_KEY);
		if (!raw) return null;
		const parsed: unknown = JSON.parse(raw);
		if (isSessionInfo(parsed)) return parsed;
	} catch {
		// fall through — a corrupt entry is treated as absent
	}
	return null;
}

function persistOwnerHumanId(nextHumanId: string | null): void {
	const stored = readStoredOwnerStay();
	if (stored === null) return;
	writeStoredOwnerStay({
		...stored,
		humanId: nextHumanId ?? undefined
	});
}

let token = $state<string | null>(null);
let humanId = $state<string | null>(null);
let displayName = $state<string | null>(null);
let grant = $state<string | null>(null);
let expiresAt = $state<string | null>(null);
// The display name attached to the persisted invite resume credential
// (#350) — non-null exactly when localStorage holds one, mirrored into
// reactive state so LoginForm's "Continue as …" offer appears/disappears
// without a reload when the credential is stored or discarded.
let resumeDisplayName = $state<string | null>(readStoredResume()?.displayName ?? null);
// True when this browser holds an owner stay-signed-in credential (#407)
// — mirrored into reactive state so +layout.svelte can decide to silent-
// resume on first paint without re-reading localStorage.
let ownerStayEnabled = $state(readStoredOwnerStay() !== null);
// True only when a previously-valid session was torn down out from under
// the user (a 401 mid-session, or the JWT's own expiry) -- distinct from
// simply being logged out, so LoginForm can say *why* it's showing again
// instead of looking like a silent bounce back to the login screen.
let sessionExpired = $state(false);

let expiryTimer: ReturnType<typeof setTimeout> | null = null;

function clearExpiryTimer(): void {
	if (expiryTimer !== null) {
		clearTimeout(expiryTimer);
		expiryTimer = null;
	}
}

// Proactively drops the session a moment before the server would start
// rejecting it anyway, so a user mid-session sees "sign in again" instead
// of the next click failing with a generic error. `expires_at` isn't
// always a well-formed/parseable date (tests stub it with placeholders),
// so a NaN or past delay is just skipped rather than firing immediately.
function scheduleExpiry(iso: string): void {
	clearExpiryTimer();
	const delay = Date.parse(iso) - Date.now();
	if (!Number.isFinite(delay) || delay <= 0) return;
	expiryTimer = setTimeout(() => clearSession(true), delay);
}

function clearSession(expired: boolean): void {
	clearExpiryTimer();
	token = null;
	humanId = null;
	displayName = null;
	grant = null;
	expiresAt = null;
	sessionExpired = expired;
}

// client.ts calls this on every 401 response. Only treat it as a session
// tear-down if we actually thought we were logged in -- a 401 from a bad
// login attempt itself (wrong phrase) shouldn't clear anything or flip on
// the "session expired" banner, and login() never sets `token` until it
// already has a successful response.
onUnauthorized(() => {
	if (token) clearSession(true);
});

export const auth = {
	get token() {
		return token;
	},
	get isAuthenticated() {
		return token !== null;
	},
	get humanId() {
		return humanId;
	},
	get displayName() {
		return displayName;
	},
	get grant() {
		return grant;
	},
	get expiresAt() {
		return expiresAt;
	},
	get sessionExpired() {
		return sessionExpired;
	},
	// Non-null when this browser holds a persisted invite re-entry
	// credential (#350) — the name to show on LoginForm's "Continue as …".
	get resumeDisplayName() {
		return resumeDisplayName;
	},
	// True when this browser holds an owner stay-signed-in credential
	// (#407) — +layout.svelte uses this to silent-resume on load.
	get ownerStayEnabled() {
		return ownerStayEnabled;
	},
	// bootstrapToken (server/api/auth.py's LoginRequest.bootstrap_token,
	// #247/#291) is only consulted server-side when this login is about to
	// create the workspace row while app_server_host is 0.0.0.0 -- fine to
	// send unconditionally, since the server ignores it otherwise.
	async login(mnemonic: string, passphrase?: string, bootstrapToken?: string): Promise<void> {
		const response = await api.post<LoginResponse>('/auth/login', {
			key: mnemonic,
			passphrase,
			bootstrap_token: bootstrapToken
		});
		token = response.token;
		grant = response.grant;
		expiresAt = response.expires_at;
		// /auth/login mints a workspace session with no human_id claim —
		// drop any leftover identity from a previous token so IdentityPicker
		// (or resumeOwnerSession's re-claim) is what sets it.
		humanId = null;
		displayName = null;
		sessionExpired = false;
		scheduleExpiry(response.expires_at);
	},
	// Sets the full claimed-identity session state at once (#14's
	// IdentityPicker, #15's invite-accept flow) -- both hand back a token
	// that already carries a human_id, unlike login() above.
	applySession(info: SessionInfo): void {
		token = info.token;
		humanId = info.human_id;
		displayName = info.display_name;
		grant = info.grant;
		expiresAt = info.expires_at;
		sessionExpired = false;
		scheduleExpiry(info.expires_at);
	},
	// Applies an invite-accept session AND persists its resume credential
	// (#350) so this browser can get back in after a refresh or sign-out —
	// the module docstring covers why this one credential is allowed into
	// localStorage when the JWT itself never is.
	rememberInviteSession(info: InviteSessionInfo): void {
		auth.applySession(info);
		writeStoredResume({ token: info.resume_token, displayName: info.display_name });
		resumeDisplayName = info.display_name;
	},
	// Exchanges the persisted resume credential for a fresh invite-grant
	// session (#350). Resolves false when there's nothing stored or the
	// server said the credential is dead (401/403 — expired idle window,
	// revoked invite), in which case it's discarded so LoginForm stops
	// offering it. Transient failures (429, a locked node's 503, network
	// errors) rethrow WITHOUT discarding — the credential may be fine, per
	// resume_invite_session's documented status-code contract.
	async resumeInviteSession(): Promise<boolean> {
		const stored = readStoredResume();
		if (stored === null) return false;
		let response: InviteSessionInfo;
		try {
			response = await api.post<InviteSessionInfo>('/invites/resume', {
				resume_token: stored.token
			});
		} catch (err) {
			if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
				writeStoredResume(null);
				resumeDisplayName = null;
				return false;
			}
			throw err;
		}
		auth.rememberInviteSession(response);
		return true;
	},
	// Claims a Human identity for the current workspace session (#14's
	// IdentityPicker) -- pass an existing human_id to "continue as" them,
	// or a display_name to mint a new identity.
	async claimIdentity(params: { humanId: string } | { displayName: string }): Promise<void> {
		const body =
			'humanId' in params ? { human_id: params.humanId } : { display_name: params.displayName };
		const response = await api.post<SessionInfo>('/auth/identity', body, token ?? undefined);
		auth.applySession(response);
		persistOwnerHumanId(response.human_id);
	},
	// Drops the claimed identity only, without a server round-trip -- #14
	// is a lightweight claim, not a credential, so re-picking who you are
	// on the same still-valid workspace session is free.
	clearIdentity(): void {
		humanId = null;
		displayName = null;
		persistOwnerHumanId(null);
	},
	// A short-lived, purpose-scoped token for the one endpoint that can't
	// use a normal Authorization header: the SSE stream (api/deps.py's
	// get_current_workspace_id_for_stream) -- EventSource can't set custom
	// headers, so *some* token has to go in the URL. Minting one of these
	// right before opening the connection, instead of putting the actual
	// session token there, is what keeps a leak of it into server logs /
	// browser history / Referer headers low-value: it expires in about a
	// minute and is rejected everywhere except that one route.
	async mintStreamTicket(): Promise<string> {
		const response = await api.post<StreamTicketResponse>(
			'/auth/stream-ticket',
			{},
			token ?? undefined
		);
		return response.ticket;
	},
	// Persists the recovery phrase so this browser can re-derive a JWT
	// after a refresh (#407). Opt-in only — LoginForm's checkbox is what
	// calls this, after a successful login. Keeps any already-stored
	// humanId so a later "stay signed in" on the same machine still
	// skips the identity picker.
	rememberOwnerStay(mnemonic: string, passphrase?: string): void {
		const prev = readStoredOwnerStay();
		writeStoredOwnerStay({
			mnemonic,
			passphrase: passphrase || undefined,
			humanId: humanId ?? prev?.humanId
		});
		ownerStayEnabled = true;
	},
	// Drops the owner stay-signed-in credential (#407). Called on
	// explicit sign-out, and on a successful login that left the
	// checkbox unchecked (so a previous opt-in does not linger).
	forgetOwnerStay(): void {
		writeStoredOwnerStay(null);
		ownerStayEnabled = false;
	},
	// One-shot park of the in-memory session for a same-tab OAuth trip
	// (#464). Called immediately before location.assign to Google.
	// Never writes the recovery phrase.
	parkOAuthHop(): void {
		if (!token) return;
		writeParkedOAuthHop({
			token,
			expires_at: expiresAt ?? '',
			grant: grant ?? 'owner',
			human_id: humanId ?? '',
			display_name: displayName ?? ''
		});
	},
	// Park, then leave this origin. Split from parkOAuthHop so tests can
	// cover the park without actually navigating.
	leaveForOAuth(url: string): void {
		auth.parkOAuthHop();
		window.location.assign(url);
	},
	// Restores a parked OAuth-hop session and deletes it. Synchronous so
	// +layout.svelte can run it before the first Unlock-vs-shell paint.
	consumeOAuthHop(): boolean {
		const parked = takeParkedOAuthHop();
		if (parked === null) return false;
		auth.applySession(parked);
		return true;
	},
	// Re-derives an owner session from the persisted phrase (#407).
	// Resolves false when nothing is stored or the server rejected the
	// phrase (400/401 — workspace reset, corrupt entry), in which case
	// the credential is discarded. Transient failures rethrow WITHOUT
	// discarding, same contract as resumeInviteSession.
	async resumeOwnerSession(): Promise<boolean> {
		const stored = readStoredOwnerStay();
		if (stored === null) return false;
		try {
			await auth.login(stored.mnemonic, stored.passphrase);
		} catch (err) {
			if (err instanceof ApiError && (err.status === 400 || err.status === 401)) {
				auth.forgetOwnerStay();
				return false;
			}
			throw err;
		}
		// login() succeeded and rewrote token/grant, but does not persist
		// the stay flag itself — put the phrase back (and keep humanId)
		// so a later refresh still has something to resume from.
		auth.rememberOwnerStay(stored.mnemonic, stored.passphrase);
		if (stored.humanId) {
			try {
				await auth.claimIdentity({ humanId: stored.humanId });
			} catch {
				// Workspace session is valid; let IdentityPicker handle a
				// stale or deleted human rather than failing the resume.
			}
		}
		return true;
	},
	// Deliberately leaves any persisted invite resume credential in place
	// (#350): sign-out ends the session, but an invited human has no other
	// way back in — LoginForm's "Continue as …" is their re-entry, the way
	// re-entering the mnemonic is the owner's.
	//
	// The owner stay-signed-in credential (#407) is the opposite: sign-out
	// is the way to stop staying signed in, so it is dropped here.
	async logout(): Promise<void> {
		const activeToken = token;
		auth.forgetOwnerStay();
		writeParkedOAuthHop(null);
		clearSession(false);
		if (activeToken) await api.post('/auth/logout', {}, activeToken);
	}
};
