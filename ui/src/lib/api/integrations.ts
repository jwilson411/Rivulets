// Connected third-party accounts (api/integrations.py, #458). Tokens
// never come back from the server — only metadata.

import { api } from './client';
import { auth } from './auth.svelte';

export interface IntegrationAccount {
	id: string;
	provider: string;
	label: string;
	account_email: string | null;
	status: string;
	scopes: string[];
	capabilities?: string[];
	last_error: string | null;
}

export interface GoogleCapability {
	id: string;
	group: string;
	group_label: string;
	label: string;
	write: boolean;
}

export interface GoogleOAuthApp {
	provider: 'google';
	client_id: string;
	has_client_secret: boolean;
	redirect_uri: string;
	capabilities?: GoogleCapability[];
	default_capabilities?: string[];
}

export const integrations = {
	list: () => api.get<IntegrationAccount[]>('/integrations', auth.token ?? undefined),
	googleOAuthApp: () =>
		api.get<GoogleOAuthApp>('/integrations/google/oauth-app', auth.token ?? undefined),
	saveGoogleOAuthApp: (body: { client_id: string; client_secret?: string | null }) =>
		api.put<GoogleOAuthApp>('/integrations/google/oauth-app', body, auth.token ?? undefined),
	connectGoogle: (label?: string, capabilities?: string[]) =>
		api.post<{ authorization_url: string }>(
			'/integrations/google/connect',
			{ label: label || null, capabilities: capabilities ?? null },
			auth.token ?? undefined
		),
	reconnect: (id: string, capabilities?: string[]) =>
		api.post<{ authorization_url: string }>(
			`/integrations/${id}/reconnect`,
			{ capabilities: capabilities ?? null },
			auth.token ?? undefined
		),
	update: (id: string, patch: { label?: string }) =>
		api.patch<IntegrationAccount>(`/integrations/${id}`, patch, auth.token ?? undefined),
	disconnect: (id: string) => api.delete<void>(`/integrations/${id}`, auth.token ?? undefined)
};
