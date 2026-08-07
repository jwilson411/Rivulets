// R-4 dispatcher hit-rate tracking client (#31, api/dispatch.py). Read-only
// aggregation of DispatchDecision rows over a selectable time window,
// mirroring usage.ts's day/week/month range pattern.

import { api } from './client';
import { auth } from './auth.svelte';

export type DispatchRange = 'day' | 'week' | 'month';

export interface MethodCount {
	method: string;
	count: number;
}

export interface HitRate {
	range: DispatchRange;
	since: string;
	total_decisions: number;
	hit_count: number;
	fallback_count: number;
	hit_rate: number | null;
	fallback_rate: number | null;
	fallback_warning: boolean;
	by_method: MethodCount[];
}

export const dispatch = {
	hitRate: (range: DispatchRange = 'week') =>
		api.get<HitRate>(`/dispatch/hit-rate?range=${range}`, auth.token ?? undefined)
};
