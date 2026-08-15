// Browser-mode component test (see sync/syncPage.svelte.test.ts for the
// shared pattern this follows). navigator.clipboard.writeText is stubbed
// since real clipboard access needs OS-level permissions Playwright's
// headless Chromium doesn't grant by default.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import InvitesPage from './+page.svelte';
import { invites, type Invite } from '$lib/api/invites';
import { ApiError } from '$lib/api/client';

// See Sidebar.svelte.test.ts for the auth.grant mocking pattern this
// follows -- defaults to 'owner' so the existing (pre-#351) tests below
// don't have to know about grants at all.
const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

vi.mock('$lib/api/invites', () => ({
	invites: {
		list: vi.fn(),
		create: vi.fn(),
		revoke: vi.fn()
	}
}));

const activeInvite: Invite = {
	id: 'inv-1',
	display_name_hint: 'Ada',
	max_uses: 1,
	use_count: 0,
	expires_at: '2099-01-01T00:00:00.000Z',
	revoked: false
};

let writeTextMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
	writeTextMock = vi.fn().mockResolvedValue(undefined);
	Object.defineProperty(navigator, 'clipboard', {
		value: { writeText: writeTextMock },
		configurable: true
	});
});

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

describe('invites/+page.svelte', () => {
	it('shows an owner-only empty state to a non-owner session without calling the API (#351)', async () => {
		authState.grant = 'invite';

		render(InvitesPage);

		await expect
			.element(page.getByText('only available to the workspace owner', { exact: false }))
			.toBeInTheDocument();
		expect(invites.list).not.toHaveBeenCalled();
	});

	it('lists outstanding invites with their status', async () => {
		vi.mocked(invites.list).mockResolvedValue([activeInvite]);

		render(InvitesPage);

		await expect.element(page.getByText('Ada')).toBeInTheDocument();
		await expect.element(page.getByText(/0\/1 used · active/)).toBeInTheDocument();
	});

	it('shows a placeholder when there are no invites', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);

		render(InvitesPage);

		await expect.element(page.getByText('No invites yet.')).toBeInTheDocument();
	});

	it('shows a load error when listing fails', async () => {
		vi.mocked(invites.list).mockRejectedValueOnce(new ApiError(403, 'Owner access required'));

		render(InvitesPage);

		await expect.element(page.getByText('Owner access required')).toBeInTheDocument();
	});

	it('creates an invite, shows the one-time link, and refreshes the list', async () => {
		vi.mocked(invites.list).mockResolvedValueOnce([]).mockResolvedValueOnce([activeInvite]);
		vi.mocked(invites.create).mockResolvedValueOnce({
			invite_id: 'inv-1',
			url: 'http://localhost:8484/invite/inv-1.secret',
			expires_at: '2099-01-01T00:00:00.000Z',
			loopback_only: false,
			lan_url: null
		});

		render(InvitesPage);
		await expect.element(page.getByText('No invites yet.')).toBeInTheDocument();

		await page.getByLabelText('Name (optional)').fill('Ada');
		await page.getByRole('button', { name: 'Create invite' }).click();

		expect(invites.create).toHaveBeenCalledWith({
			displayNameHint: 'Ada',
			maxUses: 1,
			expiresInHours: 168
		});
		await expect
			.element(page.getByText('http://localhost:8484/invite/inv-1.secret'))
			.toBeInTheDocument();
		await expect.element(page.getByText('Ada', { exact: true })).toBeInTheDocument();
	});

	it('copies the created link to the clipboard', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce({
			invite_id: 'inv-1',
			url: 'http://localhost:8484/invite/inv-1.secret',
			expires_at: '2099-01-01T00:00:00.000Z',
			loopback_only: false,
			lan_url: null
		});

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();
		await expect.element(page.getByRole('button', { name: 'Copy' })).toBeInTheDocument();

		await page.getByRole('button', { name: 'Copy' }).click();

		expect(writeTextMock).toHaveBeenCalledWith('http://localhost:8484/invite/inv-1.secret');
		await expect.element(page.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
	});

	it('warns and offers a LAN link when the created invite is loopback-only', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce({
			invite_id: 'inv-1',
			url: 'http://127.0.0.1:8484/invite/inv-1.secret',
			expires_at: '2099-01-01T00:00:00.000Z',
			loopback_only: true,
			lan_url: 'http://192.168.1.42:8484/invite/inv-1.secret'
		});

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();

		await expect.element(page.getByText(/only works on this machine/)).toBeInTheDocument();
		await expect
			.element(page.getByText('http://192.168.1.42:8484/invite/inv-1.secret'))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Copy LAN link' }).click();
		expect(writeTextMock).toHaveBeenCalledWith('http://192.168.1.42:8484/invite/inv-1.secret');
	});

	it('warns without a LAN link when no local network address was detected', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce({
			invite_id: 'inv-1',
			url: 'http://127.0.0.1:8484/invite/inv-1.secret',
			expires_at: '2099-01-01T00:00:00.000Z',
			loopback_only: true,
			lan_url: null
		});

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();

		await expect.element(page.getByText(/only works on this machine/)).toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Copy LAN link' }))
			.not.toBeInTheDocument();
	});

	it('shows an error when creating an invite fails', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockRejectedValueOnce(new ApiError(500, 'Failed to create invite'));

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();

		await expect.element(page.getByText('Failed to create invite')).toBeInTheDocument();
	});

	it('revokes an invite and refreshes the list', async () => {
		vi.mocked(invites.list)
			.mockResolvedValueOnce([activeInvite])
			.mockResolvedValueOnce([{ ...activeInvite, revoked: true }]);
		vi.mocked(invites.revoke).mockResolvedValueOnce(undefined);

		render(InvitesPage);
		await expect.element(page.getByRole('button', { name: 'Revoke' })).toBeInTheDocument();

		await page.getByRole('button', { name: 'Revoke' }).click();

		expect(invites.revoke).toHaveBeenCalledWith('inv-1');
		await expect.element(page.getByText(/revoked/)).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
	});

	it('shows an error when revoking fails', async () => {
		vi.mocked(invites.list).mockResolvedValue([activeInvite]);
		vi.mocked(invites.revoke).mockRejectedValueOnce(new ApiError(404, 'Invite not found'));

		render(InvitesPage);
		await page.getByRole('button', { name: 'Revoke' }).click();

		await expect.element(page.getByText('Invite not found')).toBeInTheDocument();
	});

	it('labels a used-up invite', async () => {
		vi.mocked(invites.list).mockResolvedValue([{ ...activeInvite, use_count: 1 }]);

		render(InvitesPage);

		await expect.element(page.getByText(/used up/)).toBeInTheDocument();
	});

	it('labels an expired invite', async () => {
		vi.mocked(invites.list).mockResolvedValue([
			{ ...activeInvite, expires_at: '2000-01-01T00:00:00.000Z' }
		]);

		render(InvitesPage);

		await expect.element(page.getByText(/expired/)).toBeInTheDocument();
	});

	it('shows a placeholder for an invite created with no name hint', async () => {
		vi.mocked(invites.list).mockResolvedValue([{ ...activeInvite, display_name_hint: null }]);

		render(InvitesPage);

		await expect.element(page.getByText('(no name hint)')).toBeInTheDocument();
	});
});
