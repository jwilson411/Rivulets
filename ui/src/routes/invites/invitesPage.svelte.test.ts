// Browser-mode component test for Invites (06-screens.md → Invites,
// mockup 2n, owner only): Create invite in a sheet, a one-time copy panel,
// and Revoke behind a confirm sheet.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import InvitesPage from './+page.svelte';
import { invites, type Invite, type InviteCreated } from '$lib/api/invites';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/invites', () => ({
	invites: { list: vi.fn(), create: vi.fn(), revoke: vi.fn() }
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

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

const inOneWeek = () => new Date(Date.now() + 7 * 86_400_000).toISOString();

const adaInvite: Invite = {
	id: 'inv-1',
	display_name_hint: 'Ada',
	max_uses: 1,
	use_count: 0,
	expires_at: inOneWeek(),
	revoked: false
};

const createdInvite: InviteCreated = {
	invite_id: 'inv-1',
	url: 'http://192.168.1.12:8484/invite/demo-token',
	expires_at: inOneWeek(),
	loopback_only: false,
	lan_url: null
};

describe('invites/+page.svelte', () => {
	it('renders the owner-only empty state for a guest without firing requests (#351)', async () => {
		authState.grant = 'invite';

		render(InvitesPage);

		await expect
			.element(page.getByText('This is only available to the workspace owner.'))
			.toBeInTheDocument();
		expect(invites.list).not.toHaveBeenCalled();
	});

	it('lists active invites with uses left and expiry', async () => {
		vi.mocked(invites.list).mockResolvedValue([adaInvite]);

		render(InvitesPage);

		await expect.element(page.getByText('Ada')).toBeInTheDocument();
		await expect.element(page.getByText(/1 use left · expires in \d+ days?/)).toBeInTheDocument();
	});

	it('creates an invite from the sheet and shows the one-time copy panel', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce(createdInvite);

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();
		await page.getByLabelText('For (optional)').fill('Ada');
		await page.getByRole('button', { name: 'Create invite' }).last().click();

		expect(invites.create).toHaveBeenCalledWith({
			displayNameHint: 'Ada',
			maxUses: 1,
			expiresInHours: 168
		});
		await expect
			.element(page.getByText("Save this now — it won't be shown again."))
			.toBeInTheDocument();
		await expect
			.element(page.getByText('http://192.168.1.12:8484/invite/demo-token'))
			.toBeInTheDocument();
	});

	it('copies the created link', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce(createdInvite);

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();
		await page.getByRole('button', { name: 'Create invite' }).last().click();
		await page.getByRole('button', { name: 'Copy link' }).click();

		expect(writeTextMock).toHaveBeenCalledWith(createdInvite.url);
	});

	it('warns when the created link only works on this machine', async () => {
		vi.mocked(invites.list).mockResolvedValue([]);
		vi.mocked(invites.create).mockResolvedValueOnce({
			...createdInvite,
			loopback_only: true,
			lan_url: 'http://192.168.1.12:8484/invite/demo-token'
		});

		render(InvitesPage);
		await page.getByRole('button', { name: 'Create invite' }).click();
		await page.getByRole('button', { name: 'Create invite' }).last().click();

		await expect
			.element(page.getByText('This link only works on this machine.', { exact: false }))
			.toBeInTheDocument();
	});

	it('revokes an invite behind a confirm sheet', async () => {
		vi.mocked(invites.list).mockResolvedValue([adaInvite]);
		vi.mocked(invites.revoke).mockResolvedValueOnce(undefined);

		render(InvitesPage);
		await page.getByRole('button', { name: 'Revoke', exact: true }).click();

		expect(invites.revoke).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Revoke invite' }).click();

		expect(invites.revoke).toHaveBeenCalledWith('inv-1');
	});

	it('shows a quiet error with retry when invites fail to load', async () => {
		vi.mocked(invites.list).mockRejectedValue(new Error('boom'));

		render(InvitesPage);

		await expect.element(page.getByText("Couldn't load invites.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
