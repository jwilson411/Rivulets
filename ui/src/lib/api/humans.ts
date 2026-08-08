// Human identity directory client (#14). Read-only -- Humans are created as
// a side effect of POST /auth/identity (auth.svelte.ts's applySession),
// not through this client.

import { api } from './client';
import { auth } from './auth.svelte';

export interface Human {
	id: string;
	display_name: string;
}

export const humans = {
	list: () => api.get<Human[]>('/humans', auth.token ?? undefined)
};
