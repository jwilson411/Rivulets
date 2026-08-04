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
}

let token = $state<string | null>(null);

export const auth = {
	get token() {
		return token;
	},
	get isAuthenticated() {
		return token !== null;
	},
	async login(mnemonic: string, passphrase?: string): Promise<void> {
		const response = await api.post<LoginResponse>('/auth/login', {
			key: mnemonic,
			passphrase
		});
		token = response.token;
	},
	async logout(): Promise<void> {
		const activeToken = token;
		token = null;
		if (activeToken) await api.post('/auth/logout', {}, activeToken);
	}
};
