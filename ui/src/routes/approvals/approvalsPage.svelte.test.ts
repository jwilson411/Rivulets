// Browser-mode component test for Approvals (06-screens.md → Approvals,
// mockup 1h): "Needs you", Waiting/Done/All chips, Approve/Reject on each
// card, read-only for guests.

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
	title: 'Researcher wants a schedule',
	detail: 'Run /retry-check every weekday at 09:00 UTC in #launch-readiness.',
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
	title: 'Daily spend hit $10',
	detail: 'Workspace cap is $10/day.',
	status: 'approved',
	resolved_by: 'user-1',
	resolved_at: '2026-01-02T00:00:00Z',
	created_at: '2026-01-01T00:00:00Z'
};

describe('approvals/+page.svelte', () => {
	it('renders a waiting approval with its source pill and detail under "Needs you"', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Needs you')).toBeInTheDocument();
		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();
		await expect
			.element(page.getByText('Run /retry-check every weekday at 09:00 UTC in #launch-readiness.'))
			.toBeInTheDocument();
	});

	it('defaults to Waiting — a resolved approval only appears under Done', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule, approvedBudget]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();
		await expect.element(page.getByText('Daily spend hit $10')).not.toBeInTheDocument();

		await page.getByRole('button', { name: 'Done' }).click();

		await expect.element(page.getByText('Daily spend hit $10')).toBeInTheDocument();
		await expect.element(page.getByText('Approved', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
	});

	it('shows "You\'re clear" when nothing is waiting', async () => {
		vi.mocked(approvals.list).mockResolvedValue([]);

		render(ApprovalsPage);

		await expect.element(page.getByText("You're clear. Nothing is waiting.")).toBeInTheDocument();
	});

	it('shows a quiet error with a retry when approvals fail to load', async () => {
		vi.mocked(approvals.list).mockRejectedValue(new Error('boom'));

		render(ApprovalsPage);

		await expect.element(page.getByText("Couldn't load approvals.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});

	it('approves a waiting approval via approvals.approve and refreshes', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValue([{ ...pendingSchedule, status: 'approved' }]);
		vi.mocked(approvals.approve).mockResolvedValueOnce({ ...pendingSchedule, status: 'approved' });

		render(ApprovalsPage);
		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		expect(approvals.approve).toHaveBeenCalledWith('appr-1');
		// The default Waiting view empties out once the item is resolved.
		await expect.element(page.getByText("You're clear. Nothing is waiting.")).toBeInTheDocument();
	});

	it('rejects a waiting approval via approvals.reject', async () => {
		vi.mocked(approvals.list)
			.mockResolvedValueOnce([pendingSchedule])
			.mockResolvedValue([{ ...pendingSchedule, status: 'rejected' }]);
		vi.mocked(approvals.reject).mockResolvedValueOnce({ ...pendingSchedule, status: 'rejected' });

		render(ApprovalsPage);
		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Reject' }).click();

		expect(approvals.reject).toHaveBeenCalledWith('appr-1');
	});

	it('shows a row-level error when approving fails', async () => {
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);
		vi.mocked(approvals.approve).mockRejectedValueOnce(new Error('boom'));

		render(ApprovalsPage);
		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Approve' }).click();

		await expect.element(page.getByText("Couldn't approve that.")).toBeInTheDocument();
	});

	it('hides the decision buttons for a guest — only the owner can approve (2q)', async () => {
		authState.grant = 'invite';
		vi.mocked(approvals.list).mockResolvedValue([pendingSchedule]);

		render(ApprovalsPage);

		await expect.element(page.getByText('Researcher wants a schedule')).toBeInTheDocument();
		await expect
			.element(page.getByText('Only the workspace owner can approve.'))
			.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
	});
});
