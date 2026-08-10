// Unified approval queue client (#102, api/approvals.py). One inbox for
// the three sources that each used to have their own bespoke "needs a
// human's OK" gate — an agent-created schedule (#93), a tripped hard_stop
// budget cap (#97), or an agent blocked from unattended sensitive-tool use
// (#100). Approve/reject perform exactly the same underlying write each
// source's own pre-#102 action already did.

import { api } from './client';
import { auth } from './auth.svelte';

export type ApprovalSourceType = 'schedule' | 'budget' | 'tool_guardrail';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

export interface PendingApproval {
	id: string;
	source_type: ApprovalSourceType;
	schedule_id: string | null;
	budget_cap_id: string | null;
	agent_id: string | null;
	title: string;
	detail: string;
	status: ApprovalStatus;
	resolved_by: string | null;
	resolved_at: string | null;
	created_at: string;
}

export const approvals = {
	list: () => api.get<PendingApproval[]>('/approvals', auth.token ?? undefined),
	approve: (id: string) =>
		api.post<PendingApproval>(`/approvals/${id}/approve`, {}, auth.token ?? undefined),
	reject: (id: string) =>
		api.post<PendingApproval>(`/approvals/${id}/reject`, {}, auth.token ?? undefined)
};
