// Browser-mode component test for the Name screen (06-screens.md → Name,
// mockup 2a): existing humans as big "Continue as …" rows, with "I'm
// someone new" revealing the input.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import IdentityPicker from './IdentityPicker.svelte';
import { auth } from '$lib/api/auth.svelte';
import { humans, type Human } from '$lib/api/humans';

vi.mock('$lib/api/auth.svelte', () => ({
	auth: { claimIdentity: vi.fn() }
}));

vi.mock('$lib/api/humans', () => ({
	humans: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

const riley: Human = { id: 'human-1', display_name: 'Riley' };

describe('IdentityPicker.svelte', () => {
	it('asks what to call you and shows the name input when no humans exist', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);

		render(IdentityPicker);

		await expect.element(page.getByText('What should we call you?')).toBeInTheDocument();
		await expect.element(page.getByLabelText('Your name')).toBeInTheDocument();
	});

	it('offers "Continue as …" rows for existing humans and claims on click', async () => {
		vi.mocked(humans.list).mockResolvedValue([riley]);
		vi.mocked(auth.claimIdentity).mockResolvedValue(undefined);

		render(IdentityPicker);

		await page.getByRole('button', { name: 'Continue as Riley' }).click();

		expect(auth.claimIdentity).toHaveBeenCalledWith({ humanId: 'human-1' });
	});

	it('reveals the new-name input behind "I\'m someone new" when humans exist', async () => {
		vi.mocked(humans.list).mockResolvedValue([riley]);
		vi.mocked(auth.claimIdentity).mockResolvedValue(undefined);

		render(IdentityPicker);
		await expect
			.element(page.getByRole('button', { name: 'Continue as Riley' }))
			.toBeInTheDocument();

		await page.getByRole('button', { name: "I'm someone new" }).click();
		await page.getByLabelText("I'm someone new").fill('  Ada  ');
		await page.getByRole('button', { name: 'Continue', exact: true }).click();

		expect(auth.claimIdentity).toHaveBeenCalledWith({ displayName: 'Ada' });
	});

	it('disables Continue until a name is typed', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);

		render(IdentityPicker);

		await expect.element(page.getByRole('button', { name: 'Continue' })).toBeDisabled();
		await page.getByLabelText('Your name').fill('Ada');
		await expect.element(page.getByRole('button', { name: 'Continue' })).toBeEnabled();
	});

	it('shows a plain-language error when the claim fails', async () => {
		vi.mocked(humans.list).mockResolvedValue([]);
		vi.mocked(auth.claimIdentity).mockRejectedValueOnce(new Error('boom'));

		render(IdentityPicker);

		await page.getByLabelText('Your name').fill('Ada');
		await page.getByRole('button', { name: 'Continue', exact: true }).click();

		await expect
			.element(page.getByText("Couldn't continue with that name. Try again."))
			.toBeInTheDocument();
	});

	it('shows a plain-language error when the humans list fails to load', async () => {
		vi.mocked(humans.list).mockRejectedValue(new Error('network down'));

		render(IdentityPicker);

		await expect.element(page.getByText("Couldn't load names. Try again.")).toBeInTheDocument();
	});
});
