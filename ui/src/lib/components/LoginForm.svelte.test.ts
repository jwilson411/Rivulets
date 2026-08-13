// Browser-mode component test (vite.config.ts's "client" vitest project,
// real Chromium via @vitest/browser-playwright). LoginForm is the
// simplest component to start with: it only depends on $lib/api/auth.svelte,
// not on any SvelteKit routing modules ($app/state, $app/paths) the way
// Sidebar.svelte and the route components do.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import LoginForm from './LoginForm.svelte';
import { auth } from '$lib/api/auth.svelte';

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		login: vi.fn(),
		logout: vi.fn()
	}
}));

// A fixed stand-in for generateMnemonic's output -- twelve distinct words so
// each one is a unique, unambiguous locator target in the rendered grid.
const STUB_PHRASE = 'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima';

vi.mock('bip39', () => ({
	generateMnemonic: vi.fn(() => STUB_PHRASE)
}));

// navigator.clipboard.writeText is stubbed since real clipboard access needs
// OS-level permissions Playwright's headless Chromium doesn't grant by
// default (see invites/invitesPage.svelte.test.ts, the shared pattern).
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

		expect(auth.login).toHaveBeenCalledWith('apple banana cherry', undefined);
		await expect.element(input).toHaveValue('');
	});

	it('calls auth.login with the passphrase when one is entered, and clears it on success', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		const passphraseInput = page.getByLabelText('Passphrase (optional)');

		await page.getByLabelText('Workspace recovery phrase (12 words)').fill('apple banana cherry');
		await passphraseInput.fill('extra word');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.login).toHaveBeenCalledWith('apple banana cherry', 'extra word');
		await expect.element(passphraseInput).toHaveValue('');
	});

	it('renders the passphrase field as a password input that is not persisted across a failed login', async () => {
		vi.mocked(auth.login).mockRejectedValueOnce(new Error('Incorrect recovery phrase'));
		render(LoginForm);
		const passphraseInput = page.getByLabelText('Passphrase (optional)');
		await expect.element(passphraseInput).toHaveAttribute('type', 'password');

		await page.getByLabelText('Workspace recovery phrase (12 words)').fill('wrong words here');
		await passphraseInput.fill('extra word');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		await expect.element(page.getByText('Incorrect recovery phrase')).toBeInTheDocument();
		await expect.element(passphraseInput).toHaveValue('extra word');
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

	it('shows no US-001 ticket reference in the helper copy', async () => {
		render(LoginForm);
		await expect.element(page.getByText('US-001', { exact: false })).not.toBeInTheDocument();
	});

	describe('generating a phrase', () => {
		it('reveals the generated words and requires acknowledgment before continuing', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();

			for (const word of STUB_PHRASE.split(' ')) {
				await expect.element(page.getByText(word, { exact: true })).toBeInTheDocument();
			}

			const continueButton = page.getByRole('button', { name: 'Enter workspace' });
			await expect.element(continueButton).toBeDisabled();

			await page.getByText("I've saved this phrase somewhere safe").click();
			await expect.element(continueButton).toBeEnabled();
		});

		it('logs in with the generated phrase once acknowledged and confirmed', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, undefined);
		});

		it('logs in with the entered passphrase alongside the generated phrase', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();
			await page.getByLabelText('Passphrase (optional)').fill('extra word');
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, 'extra word');
		});

		it('copies the generated phrase to the clipboard', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();
			await page.getByRole('button', { name: 'Copy phrase' }).click();

			expect(writeTextMock).toHaveBeenCalledWith(STUB_PHRASE);
			await expect.element(page.getByText('Copied')).toBeInTheDocument();
		});

		it('shows the error message on the generated-phrase screen when login fails', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(new Error('Server unavailable'));
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			await expect.element(page.getByText('Server unavailable')).toBeInTheDocument();
			// Login failure keeps the generated-phrase screen up (rather than
			// clearing it back to manual entry) so the user doesn't lose the
			// phrase they were about to confirm.
			await expect
				.element(page.getByText("I've saved this phrase somewhere safe"))
				.toBeInTheDocument();
		});

		it('returns to manual entry without logging in', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase for me' }).click();
			await page.getByRole('button', { name: 'Use a phrase I already have' }).click();

			await expect
				.element(page.getByLabelText('Workspace recovery phrase (12 words)'))
				.toBeInTheDocument();
			expect(auth.login).not.toHaveBeenCalled();
		});
	});
});
