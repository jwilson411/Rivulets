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
}

export const channels = {
	list: () => api.get<Channel[]>('/channels', auth.token ?? undefined),
	create: (name: string, description?: string) =>
		api.post<Channel>('/channels', { name, description }, auth.token ?? undefined)
};
