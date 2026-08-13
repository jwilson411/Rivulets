// Backup & restore client (#243, api/backups.py). Owner-only server-side
// (OwnerGrant) — a non-owner's calls here 404/403 and the Settings panel
// just surfaces that as a plain error, same as every other owner-gated
// panel on that page.

import { api } from './client';
import { auth } from './auth.svelte';

export type BackupKind = 'daily' | 'manual' | 'pre-upgrade' | 'pre-restore';

export interface Backup {
	filename: string;
	kind: BackupKind;
	size_bytes: number;
	created_at: string;
}

export const backups = {
	list: () => api.get<Backup[]>('/backups', auth.token ?? undefined),
	create: () => api.post<Backup>('/backups', {}, auth.token ?? undefined),
	// `confirm_filename` must echo `filename` back exactly (api/backups.py's
	// RestoreIn) -- the server-side half of the UI's typed-confirmation
	// guard against a one-click clobber.
	restore: (filename: string) =>
		api.post<void>(
			`/backups/${encodeURIComponent(filename)}/restore`,
			{ confirm_filename: filename },
			auth.token ?? undefined
		)
};
