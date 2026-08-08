// Reactive session state (api-design.md#authentication-flow). The JWT lives
// in memory only — never localStorage/sessionStorage — per NFR-3.4 and
// security-and-risks.md's "JWT in memory (not localStorage)" mitigation for
// DOM-based XSS. That also means a page refresh always requires re-login;
// wiring up the documented "re-derive from mnemonic in session storage"
// refresh path is a deliberate product decision, not scaffolding — left as
// a TODO here rather than silently choosing a weaker storage mode.

import { api } from './client';

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

let token = $state<string | null>(null);
let humanId = $state<string | null>(null);
let displayName = $state<string | null>(null);
let grant = $state<string | null>(null);

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
	async login(mnemonic: string, passphrase?: string): Promise<void> {
		const response = await api.post<LoginResponse>('/auth/login', {
			key: mnemonic,
			passphrase
		});
		token = response.token;
		grant = response.grant;
	},
	// Sets the full claimed-identity session state at once (#14's
	// IdentityPicker, #15's invite-accept flow) -- both hand back a token
	// that already carries a human_id, unlike login() above.
	applySession(info: SessionInfo): void {
		token = info.token;
		humanId = info.human_id;
		displayName = info.display_name;
		grant = info.grant;
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
	async logout(): Promise<void> {
		const activeToken = token;
		token = null;
		humanId = null;
		displayName = null;
		grant = null;
		if (activeToken) await api.post('/auth/logout', {}, activeToken);
	}
};
