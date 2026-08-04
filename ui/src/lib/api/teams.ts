// Team resource client (FR-2.2, api-design.md#teams).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Team {
	id: string;
	name: string;
	description: string | null;
}

export interface TeamDetail extends Team {
	agent_ids: string[];
}

export const teams = {
	list: () => api.get<Team[]>('/teams', auth.token ?? undefined),
	get: (id: string) => api.get<TeamDetail>(`/teams/${id}`, auth.token ?? undefined),
	create: (name: string, description?: string) =>
		api.post<Team>('/teams', { name, description }, auth.token ?? undefined),
	update: (id: string, patch: { name?: string; description?: string; agent_ids?: string[] }) =>
		api.patch<TeamDetail>(`/teams/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/teams/${id}`, auth.token ?? undefined)
};
