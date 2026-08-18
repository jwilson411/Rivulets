// Channel resource client (FR-2, api-design.md#channels).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Channel {
	id: string;
	name: string;
	description: string | null;
	team_id: string | null;
	position: number;
	archived: boolean;
	working_directory: string | null;
	effective_working_directory: string | null;
}

export const channels = {
	list: () => api.get<Channel[]>('/channels', auth.token ?? undefined),
	get: (id: string) => api.get<Channel>(`/channels/${id}`, auth.token ?? undefined),
	create: (name: string, description?: string, team_id?: string | null) =>
		api.post<Channel>('/channels', { name, description, team_id }, auth.token ?? undefined),
	update: (
		id: string,
		patch: {
			name?: string;
			description?: string;
			team_id?: string | null;
			working_directory?: string | null;
		}
	) => api.patch<Channel>(`/channels/${id}`, patch, auth.token ?? undefined)
};
