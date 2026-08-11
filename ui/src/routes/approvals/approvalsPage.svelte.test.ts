// Browser-mode component test (see agents/agentsPage.svelte.test.ts and
// Sidebar.svelte.test.ts for the auth.grant mocking pattern this follows).
// This route depends on $lib/api/approvals and $lib/api/auth.svelte.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ApprovalsPage from './+page.svelte';
import { approvals, type PendingApproval } from '$lib/api/approvals';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/approvals', () => ({
	approvals: { list: vi.fn(), approve: vi.fn(), reject: vi.fn() }
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const pendingSchedule: PendingApproval = {
	id: 'appr-1',
	source_type: 'schedule',
	schedule_id: 'sched-1',
	budget_cap_id: null,
	agent_id: 'agent-1',
	title: 'New weekly digest schedule',
	detail: 'Runs every Monday at 9am',
	status: 'pending',
	resolved_by: null,
	resolved_at: null,
	created_at: '2026-01-01T00:00:00Z'
};

const approvedBudget: PendingApproval = {
	id: 'appr-2',
	source_type: 'budget',
	schedule_id: null,
	budget_cap_id: 'budget-1',
	agent_id: null,
	title: 'Workspace budget cap exceeded',
	detail: 'Monthly cap of $50 reached',
	status: 'approved',
	resolved_by: 'user-1',
	resolved_at: '2026-01-02T00:00:00Z',
	created_at: '2026-01-01T00:00:00Z'
};

describe('approvals/+page.svelte', () => {
	it('renders a pending approval with its source label and detail', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);

		render(ApprovalsPage);

		const row = page.getByRole('listitem');
		await expect.element(row.getByText('New weekly digest schedule')).toBeInTheDocument();
		// "Schedule" also appears as a filter <option>, so scope to the row's badge.
		await expect.element(row.getByText('Schedule', { exact: true })).toBeInTheDocument();
		await expect.element(row.getByText('Runs every Monday at 9am')).toBeInTheDocument();
	});

	it('shows the resolved status badge instead of action buttons for a non-pending approval', async () => {
		vi.mocked(approvals.list).mockResolvedValue([approvedBudget]);

		render(ApprovalsPage);

		const row = page.getByRole('listitem');
		await expect.element(row.getByText('Workspace budget cap exceeded')).toBeInTheDocument();
		await expect.element(row.getByText('approved', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
	});

	it('shows the empty-state message when there are no approvals', async () => {
		vi.mocked(approvals.list).mockResolvedValue([]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Nothing waiting on approval.')).toBeInTheDocument();
	});

	it('shows an error when approvals fail to load', async () => {
		vi.mocked(approvals.list).mockRejectedValue(new Error('Failed to load approvals'));

		render(ApprovalsPage);

		await expect.element(page.getByText('Failed to load approvals')).toBeInTheDocument();
	});

	it('approves a pending approval via approvals.approve and refreshes', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValueOnce([{ ...pendingSchedule, status: 'approved' }]);
		vi.mocked(approvals.approve).mockResolvedValueOnce({ ...pendingSchedule, status: 'approved' });

		render(ApprovalsPage);
		await expect.element(page.getByText('New weekly digest schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		expect(approvals.approve).toHaveBeenCalledWith('appr-1');
		await expect
			.element(page.getByRole('listitem').getByText('approved', { exact: true }))
			.toBeInTheDocument();
	});

	it('rejects a pending approval via approvals.reject and refreshes', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValueOnce([{ ...pendingSchedule, status: 'rejected' }]);
		vi.mocked(approvals.reject).mockResolvedValueOnce({ ...pendingSchedule, status: 'rejected' });

		render(ApprovalsPage);
		await expect.element(page.getByText('New weekly digest schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Reject' }).click();

		expect(approvals.reject).toHaveBeenCalledWith('appr-1');
		await expect
			.element(page.getByRole('listitem').getByText('rejected', { exact: true }))
			.toBeInTheDocument();
	});

	it('shows a row-level error when approving fails, without touching other rows', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);
		vi.mocked(approvals.approve).mockRejectedValueOnce(new Error('Failed to approve'));

		render(ApprovalsPage);
		await expect.element(page.getByText('New weekly digest schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		await expect.element(page.getByText('Failed to approve')).toBeInTheDocument();
	});

	it('hides action buttons and shows "Owner access required" for a non-owner grant', async () => {
		authState.grant = 'invite';
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);

		render(ApprovalsPage);

		await expect.element(page.getByText('New weekly digest schedule')).toBeInTheDocument();
		await expect.element(page.getByText('Owner access required')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
	});
});
