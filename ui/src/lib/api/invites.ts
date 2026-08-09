// Workspace invites client (#15). accept() is the one call here that
// doesn't carry a bearer token -- the invite secret itself is the
// credential (see api/invites.py's module docstring) -- and on success
// hands the resulting session straight to auth.applySession, the same
// entry point POST /auth/identity uses (#14).

import { api } from './client';
import { auth, type SessionInfo } from './auth.svelte';

export interface Invite {
	id: string;
	display_name_hint: string | null;
	max_uses: number;
	use_count: number;
	expires_at: string;
	revoked: boolean;
}

export interface InviteCreated {
	invite_id: string;
	url: string;
	expires_at: string;
	// #121: `url` is dead-on-arrival off this machine when it was built from
	// a loopback request host (the default). `lan_url` is a best-effort
	// alternate, only ever populated alongside loopback_only === true.
	loopback_only: boolean;
	lan_url: string | null;
}

export const invites = {
	list: () => api.get<Invite[]>('/invites', auth.token ?? undefined),
	create: (params: { displayNameHint?: string; maxUses?: number; expiresInHours?: number }) =>
		api.post<InviteCreated>(
			'/invites',
			{
				display_name_hint: params.displayNameHint,
				max_uses: params.maxUses,
				expires_in_hours: params.expiresInHours
			},
			auth.token ?? undefined
		),
	revoke: (id: string) => api.delete<void>(`/invites/${id}`, auth.token ?? undefined),
	async accept(inviteToken: string, displayName?: string): Promise<void> {
		const response = await api.post<SessionInfo>('/invites/accept', {
			invite_token: inviteToken,
			display_name: displayName
		});
		auth.applySession(response);
	}
};
