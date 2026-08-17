// Pending-approvals count shown on the icon rail's Inbox badge. Lives in a
// tiny shared module (not inside the rail) so the Approvals page can bump
// it the moment an approve/reject lands — the success check in
// 10-constraints.md is literally "approve one, badge goes from 2 to 1".

import { approvals } from './api/approvals';

let count = $state<number | null>(null);

export const approvalsBadge = {
	get count() {
		return count;
	},
	async refresh(): Promise<void> {
		try {
			const list = await approvals.list();
			count = list.filter((a) => a.status === 'pending').length;
		} catch {
			// Badge is a convenience — leave it blank rather than erroring.
		}
	}
};
