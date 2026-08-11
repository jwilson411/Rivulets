// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/approvals and $lib/api/auth.svelte (for the
// owner-only approve/reject actions), not on any SvelteKit routing
// modules, so nothing else needs mocking.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ApprovalsPage from './+page.svelte';
import { approvals, type PendingApproval } from '$lib/api/approvals';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/approvals', () => ({
	approvals: {
		list: vi.fn(),
		approve: vi.fn(),
		reject: vi.fn()
	}
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		grant: 'owner'
	}
}));

const pendingSchedule: PendingApproval = {
	id: 'approval-1',
	source_type: 'schedule',
	schedule_id: 'schedule-1',
	budget_cap_id: null,
	agent_id: null,
	title: 'New schedule for Researcher',
	detail: 'Fires every day at 9am',
	status: 'pending',
	resolved_by: null,
	resolved_at: null,
	created_at: '2026-08-10T09:00:00Z'
};

const resolvedBudget: PendingApproval = {
	id: 'approval-2',
	source_type: 'budget',
	schedule_id: null,
	budget_cap_id: 'budget-1',
	agent_id: null,
	title: 'Workspace budget exceeded',
	detail: 'Hard stop tripped for August',
	status: 'approved',
	resolved_by: 'human-1',
	resolved_at: '2026-08-10T10:00:00Z',
	created_at: '2026-08-10T09:30:00Z'
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('approvals/+page.svelte', () => {
	it('renders each pending approval with its source label and actions', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);

		render(ApprovalsPage);

		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('Fires every day at 9am')).toBeInTheDocument();
		await expect.element(page.getByRole('listitem').getByText('Schedule')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
	});

	it('shows the empty-state message when nothing is pending', async () => {
		vi.mocked(approvals.list).mockResolvedValue([]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Nothing waiting on approval.')).toBeInTheDocument();
	});

	it('shows a resolved status badge instead of actions for a non-pending approval', async () => {
		vi.mocked(approvals.list).mockResolvedValue([resolvedBudget]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Workspace budget exceeded')).toBeInTheDocument();
		await expect.element(page.getByText('approved', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
	});

	it('shows an error when approvals fail to load', async () => {
		vi.mocked(approvals.list).mockRejectedValueOnce(new Error('Failed to load approvals'));

		render(ApprovalsPage);

		await expect.element(page.getByText('Failed to load approvals')).toBeInTheDocument();
	});

	it('approves via approvals.approve and refreshes the list', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValueOnce([{ ...pendingSchedule, status: 'approved' }]);
		vi.mocked(approvals.approve).mockResolvedValueOnce({ ...pendingSchedule, status: 'approved' });

		render(ApprovalsPage);
		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		expect(approvals.approve).toHaveBeenCalledWith('approval-1');
		await expect.element(page.getByText('approved', { exact: true })).toBeInTheDocument();
	});

	it('rejects via approvals.reject and refreshes the list', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValueOnce([{ ...pendingSchedule, status: 'rejected' }]);
		vi.mocked(approvals.reject).mockResolvedValueOnce({ ...pendingSchedule, status: 'rejected' });

		render(ApprovalsPage);
		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Reject' }).click();

		expect(approvals.reject).toHaveBeenCalledWith('approval-1');
		await expect.element(page.getByText('rejected', { exact: true })).toBeInTheDocument();
	});

	it('shows a row-scoped error when approving fails', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);
		vi.mocked(approvals.approve).mockRejectedValueOnce(new ApiError(403, 'Failed to approve'));

		render(ApprovalsPage);
		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		await expect.element(page.getByText('Failed to approve')).toBeInTheDocument();
	});

	it('filters approvals by status', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule, resolvedBudget]);

		render(ApprovalsPage);
		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('Workspace budget exceeded')).toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Status' }).selectOptions('approved');

		await expect.element(page.getByText('Workspace budget exceeded')).toBeInTheDocument();
		await expect.element(page.getByText('New schedule for Researcher')).not.toBeInTheDocument();
	});

	it('filters approvals by source', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule, resolvedBudget]);

		render(ApprovalsPage);
		await expect.element(page.getByText('New schedule for Researcher')).toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Source' }).selectOptions('budget');

		await expect.element(page.getByText('Workspace budget exceeded')).toBeInTheDocument();
		await expect.element(page.getByText('New schedule for Researcher')).not.toBeInTheDocument();
	});
});
