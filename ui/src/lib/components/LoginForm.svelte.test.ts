// Browser-mode component test for the Unlock screen (06-screens.md →
// Unlock, mockups 1a/1b): a landing view with two big choices, a
// phrase-entry view, and the generated-phrase view with its acknowledge
// gate — the one deliberately loud severity moment in the app.

import { page } from 'vitest/browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import LoginForm from './LoginForm.svelte';
import { auth } from '$lib/api/auth.svelte';
import { ApiError } from '$lib/api/client';

// #350: resumeDisplayName is behind a getter so individual tests can flip
// the stored-invite-credential state on and off.
const authState = vi.hoisted(() => ({
	resumeDisplayName: null as string | null,
	ownerStayEnabled: false
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		login: vi.fn(),
		logout: vi.fn(),
		resumeInviteSession: vi.fn(),
		rememberOwnerStay: vi.fn(),
		forgetOwnerStay: vi.fn(),
		get resumeDisplayName() {
			return authState.resumeDisplayName;
		},
		get ownerStayEnabled() {
			return authState.ownerStayEnabled;
		}
	}
}));

// A fixed stand-in for generateMnemonic's output -- twelve distinct words so
// each one is a unique, unambiguous locator target in the rendered grid.
const STUB_PHRASE = 'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima';

// A real 12-word BIP-39 phrase (same fixture as e2e/smoke.e2e.ts). Used
// wherever Enter workspace has to enable — #421 gates submit on this check.
const VALID_PHRASE =
	'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

vi.mock('bip39', () => ({
	generateMnemonic: vi.fn(() => STUB_PHRASE)
}));

// navigator.clipboard.writeText is stubbed since real clipboard access needs
// OS-level permissions Playwright's headless Chromium doesn't grant by
// default.
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
	authState.resumeDisplayName = null;
	authState.ownerStayEnabled = false;
});

async function openPhraseEntry() {
	await page.getByRole('button', { name: 'I already have a phrase' }).click();
}

describe('LoginForm.svelte', () => {
	it('leads with the two big choices and the local-first headline', async () => {
		render(LoginForm);

		await expect
			.element(page.getByText('Your workspace lives on this machine.'))
			.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Generate a recovery phrase' }))
			.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'I already have a phrase' }))
			.toBeInTheDocument();
	});

	it('says why Unlock reappeared when the session expired', async () => {
		render(LoginForm, { sessionExpired: true });

		await expect.element(page.getByText('Your session ended — unlock again.')).toBeInTheDocument();
	});

	it('disables submit until the phrase is 12 BIP-39 words (#421)', async () => {
		render(LoginForm);
		await openPhraseEntry();

		const button = page.getByRole('button', { name: 'Enter workspace' });
		const input = page.getByLabelText('Workspace recovery phrase');
		await expect.element(button).toBeDisabled();

		await input.fill('asdf qwer zxcv random junk words here not valid');
		await expect.element(button).toBeDisabled();

		await input.fill('one two three four five six seven eight nine ten eleven twelve');
		await expect.element(button).toBeDisabled();

		await input.fill(VALID_PHRASE);
		await expect.element(button).toBeEnabled();
	});

	it('calls auth.login with the trimmed mnemonic and clears the input on success', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		await openPhraseEntry();
		const input = page.getByLabelText('Workspace recovery phrase');

		await input.fill(`  ${VALID_PHRASE}  `);
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.login).toHaveBeenCalledWith(VALID_PHRASE, undefined, undefined);
		expect(auth.forgetOwnerStay).toHaveBeenCalledOnce();
		expect(auth.rememberOwnerStay).not.toHaveBeenCalled();
		await expect.element(input).toHaveValue('');
	});

	it('warns that refresh signs you out, and persists the phrase when stay-signed-in is checked (#407)', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		await openPhraseEntry();

		await expect
			.element(page.getByText('Refreshing or opening a new tab will sign you out.'))
			.toBeInTheDocument();

		await page.getByText('Stay signed in on this machine').click();
		await expect
			.element(page.getByText('Stores your recovery phrase in this browser', { exact: false }))
			.toBeInTheDocument();

		await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.login).toHaveBeenCalledWith(VALID_PHRASE, undefined, undefined);
		expect(auth.rememberOwnerStay).toHaveBeenCalledWith(VALID_PHRASE, undefined);
		expect(auth.forgetOwnerStay).not.toHaveBeenCalled();
	});

	it('starts with stay-signed-in checked when this browser already opted in', async () => {
		authState.ownerStayEnabled = true;
		render(LoginForm);
		await openPhraseEntry();

		await expect
			.element(page.getByText('Stores your recovery phrase in this browser', { exact: false }))
			.toBeInTheDocument();
	});

	it('reveals the passphrase field on demand and sends it with the login', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		await openPhraseEntry();

		await expect.element(page.getByLabelText('Passphrase')).not.toBeInTheDocument();
		await page.getByRole('button', { name: 'Add a passphrase' }).click();
		const passphraseInput = page.getByLabelText('Passphrase');
		await expect.element(passphraseInput).toHaveAttribute('type', 'password');

		await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
		await passphraseInput.fill('extra word');
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.login).toHaveBeenCalledWith(VALID_PHRASE, 'extra word', undefined);
	});

	it('shows the error message and keeps the input when login fails', async () => {
		vi.mocked(auth.login).mockRejectedValueOnce(new Error('Incorrect recovery phrase'));
		render(LoginForm);
		await openPhraseEntry();
		const input = page.getByLabelText('Workspace recovery phrase');

		await input.fill(VALID_PHRASE);
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		await expect.element(page.getByText('Incorrect recovery phrase')).toBeInTheDocument();
		await expect.element(input).toHaveValue(VALID_PHRASE);
	});

	it('shows a generic message when login rejects with something other than an Error', async () => {
		vi.mocked(auth.login).mockRejectedValueOnce('not an Error instance');
		render(LoginForm);
		await openPhraseEntry();

		await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		await expect.element(page.getByText('Login failed')).toBeInTheDocument();
	});

	describe('generating a phrase', () => {
		it('reveals the generated words behind the loud warning and requires acknowledgment', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();

			await expect
				.element(page.getByText('This phrase is the only way back in.'))
				.toBeInTheDocument();
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

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, undefined, undefined);
			expect(auth.forgetOwnerStay).toHaveBeenCalledOnce();
		});

		it('can stay signed in from the generated-phrase screen (#407)', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByText('Stay signed in on this machine').click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.rememberOwnerStay).toHaveBeenCalledWith(STUB_PHRASE, undefined);
		});

		it('logs in with the entered passphrase alongside the generated phrase', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Add a passphrase' }).click();
			await page.getByLabelText('Passphrase').fill('extra word');
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, 'extra word', undefined);
		});

		it('copies the generated phrase to the clipboard', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Copy phrase' }).click();

			expect(writeTextMock).toHaveBeenCalledWith(STUB_PHRASE);
			await expect.element(page.getByText('Copied')).toBeInTheDocument();
		});

		it('shows the error message on the generated-phrase screen when login fails', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(new Error('Server unavailable'));
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			await expect.element(page.getByText('Server unavailable')).toBeInTheDocument();
			// Login failure keeps the generated-phrase screen up (rather than
			// clearing it back to the landing) so the user doesn't lose the
			// phrase they were about to confirm.
			await expect
				.element(page.getByText("I've saved this phrase somewhere safe"))
				.toBeInTheDocument();
		});

		it('returns to the landing without logging in', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Back' }).click();

			await expect
				.element(page.getByRole('button', { name: 'I already have a phrase' }))
				.toBeInTheDocument();
			expect(auth.login).not.toHaveBeenCalled();
		});
	});

	describe('invite session resume (#350)', () => {
		it('offers no invite re-entry when this browser holds no resume credential', async () => {
			render(LoginForm);

			await expect
				.element(page.getByRole('button', { name: 'Continue as Ada' }))
				.not.toBeInTheDocument();
		});

		it('offers "Continue as …" when a resume credential is stored, and resumes on click', async () => {
			authState.resumeDisplayName = 'Ada';
			vi.mocked(auth.resumeInviteSession).mockResolvedValueOnce(true);
			render(LoginForm);

			await page.getByRole('button', { name: 'Continue as Ada' }).click();

			expect(auth.resumeInviteSession).toHaveBeenCalledOnce();
		});

		it('explains when the stored credential has been rejected as no longer valid', async () => {
			authState.resumeDisplayName = 'Ada';
			// resumeInviteSession resolving false means the server 401/403'd
			// and the credential was discarded (auth.svelte.ts) — mirror the
			// discard here so the button disappears along with it.
			vi.mocked(auth.resumeInviteSession).mockImplementationOnce(async () => {
				authState.resumeDisplayName = null;
				return false;
			});
			render(LoginForm);

			await page.getByRole('button', { name: 'Continue as Ada' }).click();

			await expect
				.element(page.getByText('Your invite access is no longer valid', { exact: false }))
				.toBeInTheDocument();
			await expect
				.element(page.getByRole('button', { name: 'Continue as Ada' }))
				.not.toBeInTheDocument();
		});

		it('shows a transient failure without dropping the button, so the user can retry', async () => {
			authState.resumeDisplayName = 'Ada';
			vi.mocked(auth.resumeInviteSession).mockRejectedValueOnce(
				new ApiError(503, "This workspace isn't currently unlocked on this node")
			);
			render(LoginForm);

			await page.getByRole('button', { name: 'Continue as Ada' }).click();

			await expect
				.element(page.getByText("isn't currently unlocked", { exact: false }))
				.toBeInTheDocument();
			await expect
				.element(page.getByRole('button', { name: 'Continue as Ada' }))
				.toBeInTheDocument();
		});
	});

	describe('bootstrap token gate (#291)', () => {
		const bootstrapError = new ApiError(
			401,
			'This node requires RIVULETS_BOOTSTRAP_TOKEN to initialize a workspace while bound to 0.0.0.0'
		);

		it('does not show a setup-token field until the server asks for one', async () => {
			render(LoginForm);
			await openPhraseEntry();

			await expect.element(page.getByLabelText('Setup token')).not.toBeInTheDocument();
		});

		it('does not show a setup-token field for an unrelated 401', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(new ApiError(401, 'Incorrect recovery phrase'));
			render(LoginForm);
			await openPhraseEntry();

			await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			await expect.element(page.getByText('Incorrect recovery phrase')).toBeInTheDocument();
			await expect.element(page.getByLabelText('Setup token')).not.toBeInTheDocument();
		});

		it('reveals a setup-token field once the server requires one, and sends it on retry', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(bootstrapError).mockResolvedValueOnce(undefined);
			render(LoginForm);
			await openPhraseEntry();

			await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			const tokenInput = page.getByLabelText('Setup token');
			await expect.element(tokenInput).toBeInTheDocument();

			await tokenInput.fill('correct-token');
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenLastCalledWith(VALID_PHRASE, undefined, 'correct-token');
		});

		it('reveals a setup-token field on the generated-phrase screen too', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(bootstrapError).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByText("I've saved this phrase somewhere safe").click();
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			const tokenInput = page.getByLabelText('Setup token');
			await expect.element(tokenInput).toBeInTheDocument();

			await tokenInput.fill('correct-token');
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenLastCalledWith(STUB_PHRASE, undefined, 'correct-token');
		});
	});
});
