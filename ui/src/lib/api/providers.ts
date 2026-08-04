// LLM provider config client (FR-1.4, api-design.md#providers).
// Raw API keys are never returned by the server (NFR-3.3) — only
// provider/label/base_url/is_default come back.

import { api } from './client';
import { auth } from './auth.svelte';

export type ProviderKind = 'openai' | 'anthropic' | 'deepseek' | 'openai_compatible';

export interface Provider {
	id: string;
	provider: ProviderKind;
	label: string;
	base_url: string | null;
	is_default: boolean;
}

export interface ProviderCreateInput {
	provider: ProviderKind;
	label: string;
	api_key: string;
	base_url?: string;
}

export const providers = {
	list: () => api.get<Provider[]>('/providers', auth.token ?? undefined),
	create: (body: ProviderCreateInput) =>
		api.post<Provider>('/providers', body, auth.token ?? undefined),
	update: (id: string, patch: { label?: string; api_key?: string; base_url?: string }) =>
		api.patch<Provider>(`/providers/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/providers/${id}`, auth.token ?? undefined)
};
