// Workspace settings client (FR-7.4, OQ-2, OQ-5, api-design.md#settings).
// Backed by a flat key/value store — GET always returns every known key
// (defaults filled in for anything not yet overridden); PATCH accepts a
// partial object and only touches the keys present in it.

import { api } from './client';
import { auth } from './auth.svelte';

export interface WorkspaceSettings {
	'dispatcher.model_override': string | null;
	'dispatcher.fallback_enabled': boolean;
	'guard.turn_limit': number;
	'guard.cycle_window': number;
	'guard.cycle_threshold': number;
	'guard.timeout_minutes': number;
	'rivulet.summarization_enabled': boolean;
	'rivulet.context_threshold_pct': number;
	'rivulet.recent_messages_kept': number;
	'sync.eager_files_lan': boolean;
	'sync.eager_files_wan': boolean;
	'ui.port': number;
	'tools.working_directory': string | null;
}

export interface DirectoryEntry {
	name: string;
	path: string;
}

export interface DirectoryListing {
	path: string;
	parent: string | null;
	entries: DirectoryEntry[];
}

export const settings = {
	get: () => api.get<WorkspaceSettings>('/settings', auth.token ?? undefined),
	update: (patch: Partial<WorkspaceSettings>) =>
		api.patch<WorkspaceSettings>('/settings', patch, auth.token ?? undefined),
	listDirectories: (path?: string | null) => {
		const query = path ? `?path=${encodeURIComponent(path)}` : '';
		return api.get<DirectoryListing>(`/settings/directories${query}`, auth.token ?? undefined);
	},
	createDirectory: (parent: string, name: string) =>
		api.post<DirectoryListing>('/settings/directories', { parent, name }, auth.token ?? undefined)
};
