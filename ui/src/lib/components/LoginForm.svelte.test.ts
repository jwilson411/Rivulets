// Browser-mode component test (vite.config.ts's "client" vitest project,
// real Chromium via @vitest/browser-playwright). LoginForm is the
// simplest component to start with: it only depends on $lib/api/auth.svelte,
// not on any SvelteKit routing modules ($app/state, $app/paths) the way
// Sidebar.svelte and the route components do.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import LoginForm from './LoginForm.svelte';
import { auth } from '$lib/api/auth.svelte';

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		login: vi.fn(),
		logout: vi.fn()
	}
}));

afterEach(() => {
	vi.clearAllMocks();
});

describe('LoginForm.svelte', () => {
	it('disables the submit button until a mnemonic is entered', async () => {
		render(LoginForm);
		const button = page.getByRole('button', { name: 'Enter workspace' });
		await expect.element(button).toBeDisabled();

		await page.getByLabelText('Workspace recovery phrase (12 words)').fill('a b c');

		await expect.element(button).toBeEnabled();
	});

	it('calls auth.login with the trimmed mnemonic and clears the input on success', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		const input = page.getByLabelText('Workspace recovery phrase (12 words)');

		await input.fill('  apple banana cherry  ');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.login).toHaveBeenCalledWith('apple banana cherry');
		await expect.element(input).toHaveValue('');
	});

	it('shows the error message and keeps the input when login fails', async () => {
		vi.mocked(auth.login).mockRejectedValueOnce(new Error('Incorrect recovery phrase'));
		render(LoginForm);
		const input = page.getByLabelText('Workspace recovery phrase (12 words)');

		await input.fill('wrong words here');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		await expect.element(page.getByText('Incorrect recovery phrase')).toBeInTheDocument();
		await expect.element(input).toHaveValue('wrong words here');
	});

	it('shows a generic message when login rejects with something other than an Error', async () => {
		vi.mocked(auth.login).mockRejectedValueOnce('not an Error instance');
		render(LoginForm);

		await page.getByLabelText('Workspace recovery phrase (12 words)').fill('whatever');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		await expect.element(page.getByText('Login failed')).toBeInTheDocument();
	});
});
