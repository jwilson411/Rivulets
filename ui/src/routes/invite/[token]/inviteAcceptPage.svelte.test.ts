// Browser-mode component test (see LoginForm.svelte.test.ts for the shared
// pattern). This route additionally depends on $app/state (for the
// [token] param) and $app/navigation (redirect on success), unlike
// LoginForm.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import InviteAcceptPage from './+page.svelte';
import { invites } from '$lib/api/invites';

const gotoMock = vi.hoisted(() => vi.fn());

vi.mock('$app/state', () => ({
	page: { params: { token: 'inv-1.secret' } }
}));

vi.mock('$app/navigation', () => ({
	goto: gotoMock
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$lib/api/invites', () => ({
	invites: { accept: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

describe('routes/invite/[token]/+page.svelte', () => {
	it('accepts the invite from the URL token and the entered display name, then navigates home', async () => {
		vi.mocked(invites.accept).mockResolvedValueOnce(undefined);

		render(InviteAcceptPage);
		await page.getByLabelText('Your name').fill('  Ada  ');
		await page.getByRole('button', { name: 'Join workspace' }).click();

		expect(invites.accept).toHaveBeenCalledWith('inv-1.secret', 'Ada');
		expect(gotoMock).toHaveBeenCalledWith('/');
	});

	it('disables Join workspace until a name is typed', async () => {
		render(InviteAcceptPage);

		await expect.element(page.getByRole('button', { name: 'Join workspace' })).toBeDisabled();
		await page.getByLabelText('Your name').fill('Ada');
		await expect.element(page.getByRole('button', { name: 'Join workspace' })).toBeEnabled();
	});

	it('shows an error message when accepting fails', async () => {
		vi.mocked(invites.accept).mockRejectedValueOnce(new Error('Invalid invite link'));

		render(InviteAcceptPage);
		await page.getByLabelText('Your name').fill('Ada');
		await page.getByRole('button', { name: 'Join workspace' }).click();

		await expect.element(page.getByText('Invalid invite link')).toBeInTheDocument();
		expect(gotoMock).not.toHaveBeenCalled();
	});
});
