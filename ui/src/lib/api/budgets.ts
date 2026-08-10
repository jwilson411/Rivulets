// Budget cap resource client (#97, api/budgets.py). Cap definitions are
// workspace policy (synced like Team/Agent); the spend/status numbers on
// each row are this node's own local view (see BudgetCap's docstring in
// db/models.py for why enforcement can't be aggregated across peers yet).

import { api } from './client';
import { auth } from './auth.svelte';

export type BudgetScope = 'agent' | 'team' | 'workspace';
export type BudgetPeriod = 'day' | 'week' | 'month';
export type BudgetAction = 'alert' | 'hard_stop';

export interface BudgetCap {
	id: string;
	scope_type: BudgetScope;
	agent_id: string | null;
	team_id: string | null;
	period: BudgetPeriod;
	limit_usd: number;
	action: BudgetAction;
	enabled: boolean;
}

export interface BudgetStatus extends BudgetCap {
	period_start: string;
	spend_usd: number;
	unpriced_run_count: number;
	breached: boolean;
	// breached AND action === 'hard_stop' AND not currently overridden.
	blocked: boolean;
	override_active: boolean;
}

export interface BudgetCapCreate {
	scope_type: BudgetScope;
	agent_id?: string | null;
	team_id?: string | null;
	period: BudgetPeriod;
	limit_usd: number;
	action: BudgetAction;
}

export const budgets = {
	list: () => api.get<BudgetStatus[]>('/budgets', auth.token ?? undefined),
	create: (body: BudgetCapCreate) => api.post<BudgetCap>('/budgets', body, auth.token ?? undefined),
	update: (
		id: string,
		patch: Partial<Pick<BudgetCap, 'period' | 'limit_usd' | 'action' | 'enabled'>>
	) => api.patch<BudgetCap>(`/budgets/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/budgets/${id}`, auth.token ?? undefined),
	override: (id: string) =>
		api.post<BudgetStatus>(`/budgets/${id}/override`, {}, auth.token ?? undefined)
};
