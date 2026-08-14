// Reactive session state (api-design.md#authentication-flow). The JWT lives
// in memory only — never localStorage/sessionStorage — per NFR-3.4 and
// security-and-risks.md's "JWT in memory (not localStorage)" mitigation for
// DOM-based XSS. That also means a page refresh always requires re-login;
// wiring up the documented "re-derive from mnemonic in session storage"
// refresh path is a deliberate product decision, not scaffolding — left as
// a TODO here rather than silently choosing a weaker storage mode.

import { api, onUnauthorized } from './client';

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

interface StreamTicketResponse {
	ticket: string;
	expires_at: string;
}

let token = $state<string | null>(null);
let humanId = $state<string | null>(null);
let displayName = $state<string | null>(null);
let grant = $state<string | null>(null);
let expiresAt = $state<string | null>(null);
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
	// Claims a Human identity for the current workspace session (#14's
	// IdentityPicker) -- pass an existing human_id to "continue as" them,
	// or a display_name to mint a new identity.
	async claimIdentity(params: { humanId: string } | { displayName: string }): Promise<void> {
		const body =
			'humanId' in params ? { human_id: params.humanId } : { display_name: params.displayName };
		const response = await api.post<SessionInfo>('/auth/identity', body, token ?? undefined);
		auth.applySession(response);
	},
	// Drops the claimed identity only, without a server round-trip -- #14
	// is a lightweight claim, not a credential, so re-picking who you are
	// on the same still-valid workspace session is free.
	clearIdentity(): void {
		humanId = null;
		displayName = null;
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
	async logout(): Promise<void> {
		const activeToken = token;
		clearSession(false);
		if (activeToken) await api.post('/auth/logout', {}, activeToken);
	}
};
