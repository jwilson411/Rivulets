// P2P sync status/control/conflicts client (FR-9, api-design.md#sync).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Peer {
	peer_id: string;
	address: string;
	connected: boolean;
}

export interface SyncStatus {
	running: boolean;
	node_id: string | null;
	peers: Peer[];
	pending_changes: number;
}

export interface SyncConflict {
	id: string;
	entity_type: string;
	entity_id: string;
	// Values are whatever JSON the conflicting entity's synced fields
	// happen to be (strings, numbers, booleans, null) — no fixed shape.
	local_snapshot: Record<string, unknown>;
	remote_snapshot: Record<string, unknown>;
	remote_node_id: string;
	detected_at: string;
}

export const sync = {
	status: () => api.get<SyncStatus>('/sync/status', auth.token ?? undefined),
	connect: (address: string) =>
		api.post<Peer>('/sync/connect', { address }, auth.token ?? undefined),
	disconnect: (peer_id: string) =>
		api.post<void>('/sync/disconnect', { peer_id }, auth.token ?? undefined),
	conflicts: () => api.get<SyncConflict[]>('/sync/conflicts', auth.token ?? undefined),
	resolveConflict: (id: string, keep: 'local' | 'remote') =>
		api.post<SyncConflict>(`/sync/conflicts/${id}/resolve`, { keep }, auth.token ?? undefined)
};
