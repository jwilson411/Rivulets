// Browser-mode component test (see LoginForm.svelte.test.ts for the shared
// pattern this follows -- IdentityPicker similarly only depends on
// $lib/api/auth.svelte and $lib/api/humans, not SvelteKit routing modules).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import IdentityPicker from './IdentityPicker.svelte';
import { auth } from '$lib/api/auth.svelte';
import { humans } from '$lib/api/humans';

vi.mock('$lib/api/auth.svelte', () => ({
	auth: { claimIdentity: vi.fn() }
}));

vi.mock('$lib/api/humans', () => ({
	humans: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

describe('IdentityPicker.svelte', () => {
	it('offers "Continue as X" for each existing human and claims by id on click', async () => {
		vi.mocked(humans.list).mockResolvedValue([
			{ id: 'human-1', display_name: 'Ada' },
			{ id: 'human-2', display_name: 'Grace' }
		]);
		vi.mocked(auth.claimIdentity).mockResolvedValueOnce(undefined);

		render(IdentityPicker);

		await expect.element(page.getByText('Continue as Ada')).toBeInTheDocument();
		await expect.element(page.getByText('Continue as Grace')).toBeInTheDocument();

		await page.getByText('Continue as Ada').click();

		expect(auth.claimIdentity).toHaveBeenCalledWith({ humanId: 'human-1' });
	});

	it('claims a new display name via the form', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);
		vi.mocked(auth.claimIdentity).mockResolvedValueOnce(undefined);

		render(IdentityPicker);
		const input = page.getByPlaceholder('e.g. Ada');
		await input.fill('Ada');
		await page.getByRole('button', { name: 'Continue' }).click();

		expect(auth.claimIdentity).toHaveBeenCalledWith({ displayName: 'Ada' });
		await expect.element(input).toHaveValue('');
	});

	it('disables the submit button until a name is entered', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);

		render(IdentityPicker);
		const button = page.getByRole('button', { name: 'Continue' });
		await expect.element(button).toBeDisabled();

		await page.getByPlaceholder('e.g. Ada').fill('Ada');

		await expect.element(button).toBeEnabled();
	});

	it('shows an error message when claiming fails', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);
		vi.mocked(auth.claimIdentity).mockRejectedValueOnce(new Error('Human not found'));

		render(IdentityPicker);
		await page.getByPlaceholder('e.g. Ada').fill('Ada');
		await page.getByRole('button', { name: 'Continue' }).click();

		await expect.element(page.getByText('Human not found')).toBeInTheDocument();
	});

	it('shows a load error when the humans list fails to fetch', async () => {
		vi.mocked(humans.list).mockRejectedValueOnce(new Error('Failed to load identities'));

		render(IdentityPicker);

		await expect.element(page.getByText('Failed to load identities')).toBeInTheDocument();
	});
});
