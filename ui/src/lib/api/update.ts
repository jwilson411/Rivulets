// Auto-update client (#11, api/update.py). GET reports whether a newer
// release is available and whether this deployment can even apply one
// (false for `uv run`/Docker -- see update.py's is_frozen); POST is the
// single approval action that downloads, verifies, swaps the binary in,
// and restarts.

import { api } from './client';
import { auth } from './auth.svelte';

export interface UpdateStatus {
	current_version: string;
	latest_version: string | null;
	update_available: boolean;
	applicable: boolean;
}

export const update = {
	status: () => api.get<UpdateStatus>('/update/status', auth.token ?? undefined),
	apply: () => api.post<{ status: string }>('/update/apply', {}, auth.token ?? undefined)
};
