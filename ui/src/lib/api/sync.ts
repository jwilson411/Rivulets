// P2P sync status/control/conflicts client (FR-9, api-design.md#sync).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Peer {
	peer_id: string;
	address: string;
	connected: boolean;
	capabilities: string[];
}

export interface SyncStatus {
	running: boolean;
	node_id: string | null;
	peers: Peer[];
	pending_changes: number;
}

export interface CoordinatorStatus {
	running: boolean;
	node_id: string | null;
	coordinator_id: string | null;
	term: number;
	is_self: boolean;
	self_score: number;
	peer_scores: Record<string, number>;
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
	coordinator: () => api.get<CoordinatorStatus>('/sync/coordinator', auth.token ?? undefined),
	reclaimCoordinator: () =>
		api.post<CoordinatorStatus>('/sync/coordinator/reclaim', {}, auth.token ?? undefined),
	conflicts: () => api.get<SyncConflict[]>('/sync/conflicts', auth.token ?? undefined),
	resolveConflict: (id: string, keep: 'local' | 'remote') =>
		api.post<SyncConflict>(`/sync/conflicts/${id}/resolve`, { keep }, auth.token ?? undefined),
	getCapabilities: () =>
		api.get<{ capabilities: string[] }>('/sync/capabilities', auth.token ?? undefined),
	setCapabilities: (capabilities: string[]) =>
		api.patch<{ capabilities: string[] }>(
			'/sync/capabilities',
			{ capabilities },
			auth.token ?? undefined
		)
};
