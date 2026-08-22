// Browser-mode component test for the Unlock screen (06-screens.md →
// Unlock, mockups 1a/1b): a landing view with two big choices, a
// phrase-entry view, and the generated-phrase view with its acknowledge
// gate — the one deliberately loud severity moment in the app — followed
// by the #518 verify step that quizzes three words of the phrase before
// the first login completes.

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

// #518: pin the verify step's sampled positions so tests know which words
// it will ask for — indices 2/6/10 → words 3 ("charlie"), 7 ("golf") and
// 11 ("kilo") of STUB_PHRASE.
vi.mock('$lib/mnemonic', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/mnemonic')>()),
	sampleWordIndices: vi.fn(() => [2, 6, 10])
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

// Walks the #518 confirm gate: acknowledge, continue to the verify step,
// and answer the quiz correctly, leaving the flow at the final
// Enter workspace button (which it clicks).
async function acknowledgeAndVerify() {
	await page.getByText("I've saved this phrase somewhere safe").click();
	await page.getByRole('button', { name: 'Continue' }).click();
	await page.getByLabelText('Word 3').fill('charlie');
	await page.getByLabelText('Word 7').fill('golf');
	await page.getByLabelText('Word 11').fill('kilo');
	await page.getByRole('button', { name: 'Enter workspace' }).click();
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

	it('names the passphrase in stay-signed-in copy when one will be stored (#478)', async () => {
		vi.mocked(auth.login).mockResolvedValueOnce(undefined);
		render(LoginForm);
		await openPhraseEntry();

		await page.getByRole('button', { name: 'Add a passphrase' }).click();
		await page.getByLabelText('Passphrase').fill('extra word');
		await page.getByText('Stay signed in on this machine').click();

		await expect
			.element(
				page.getByText('Stores your recovery phrase and passphrase in this browser', {
					exact: false
				})
			)
			.toBeInTheDocument();
		await expect
			.element(page.getByText('Stay signed in stores this next to the phrase', { exact: false }))
			.toBeInTheDocument();
		await expect
			.element(page.getByText("You'll need this every time", { exact: false }))
			.not.toBeInTheDocument();

		await page.getByLabelText('Workspace recovery phrase').fill(VALID_PHRASE);
		await page.getByRole('button', { name: 'Enter workspace' }).click();

		expect(auth.rememberOwnerStay).toHaveBeenCalledWith(VALID_PHRASE, 'extra word');
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

			const continueButton = page.getByRole('button', { name: 'Continue' });
			await expect.element(continueButton).toBeDisabled();

			await page.getByText("I've saved this phrase somewhere safe").click();
			await expect.element(continueButton).toBeEnabled();
		});

		it('logs in with the generated phrase once acknowledged and verified (#518)', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await acknowledgeAndVerify();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, undefined, undefined);
			expect(auth.forgetOwnerStay).toHaveBeenCalledOnce();
		});

		it('can stay signed in from the generated-phrase screen (#407)', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByText('Stay signed in on this machine').click();
			await acknowledgeAndVerify();

			expect(auth.rememberOwnerStay).toHaveBeenCalledWith(STUB_PHRASE, undefined);
		});

		it('logs in with the entered passphrase alongside the generated phrase', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Add a passphrase' }).click();
			await page.getByLabelText('Passphrase', { exact: true }).fill('extra word');
			await page.getByLabelText('Confirm passphrase').fill('extra word');
			await acknowledgeAndVerify();

			expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, 'extra word', undefined);
		});

		it('names the passphrase in stay-signed-in copy when one is set (#478)', async () => {
			vi.mocked(auth.login).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Add a passphrase' }).click();
			await page.getByLabelText('Passphrase', { exact: true }).fill('extra word');
			await page.getByLabelText('Confirm passphrase').fill('extra word');
			await page.getByText('Stay signed in on this machine').click();

			await expect
				.element(
					page.getByText('Stores your recovery phrase and passphrase in this browser', {
						exact: false
					})
				)
				.toBeInTheDocument();

			await acknowledgeAndVerify();

			expect(auth.rememberOwnerStay).toHaveBeenCalledWith(STUB_PHRASE, 'extra word');
		});

		it('requires the passphrase to be typed twice before continuing (#518)', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Add a passphrase' }).click();
			await page.getByLabelText('Passphrase', { exact: true }).fill('extra word');
			await page.getByText("I've saved this phrase somewhere safe").click();

			const continueButton = page.getByRole('button', { name: 'Continue' });
			await expect.element(continueButton).toBeDisabled();

			await page.getByLabelText('Confirm passphrase').fill('extra wrod');
			await expect.element(page.getByText("Passphrases don't match.")).toBeInTheDocument();
			await expect.element(continueButton).toBeDisabled();

			await page.getByLabelText('Confirm passphrase').fill('extra word');
			await expect.element(continueButton).toBeEnabled();
		});

		it('copies the generated phrase to the clipboard', async () => {
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await page.getByRole('button', { name: 'Copy phrase' }).click();

			expect(writeTextMock).toHaveBeenCalledWith(STUB_PHRASE);
			await expect.element(page.getByText('Copied')).toBeInTheDocument();
		});

		it('shows the error message on the verify screen when login fails', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(new Error('Server unavailable'));
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await acknowledgeAndVerify();

			await expect.element(page.getByText('Server unavailable')).toBeInTheDocument();
			// Login failure keeps the verify screen up (rather than clearing
			// back to the landing) so the user doesn't lose the phrase they
			// were about to confirm — it's still one "Show my phrase again"
			// away.
			await expect
				.element(page.getByRole('button', { name: 'Show my phrase again' }))
				.toBeInTheDocument();
		});

		describe('re-entry verification (#518)', () => {
			async function openVerifyStep() {
				await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
				await page.getByText("I've saved this phrase somewhere safe").click();
				await page.getByRole('button', { name: 'Continue' }).click();
			}

			it('asks for the sampled words and keeps submit disabled until all are filled', async () => {
				render(LoginForm);
				await openVerifyStep();

				await expect
					.element(page.getByText('Enter words 3, 7 and 11 of your recovery phrase.'))
					.toBeInTheDocument();

				const submit = page.getByRole('button', { name: 'Enter workspace' });
				await expect.element(submit).toBeDisabled();

				await page.getByLabelText('Word 3').fill('charlie');
				await page.getByLabelText('Word 7').fill('golf');
				await expect.element(submit).toBeDisabled();

				await page.getByLabelText('Word 11').fill('kilo');
				await expect.element(submit).toBeEnabled();
			});

			it('blocks a wrong re-entry without calling login, then proceeds once corrected', async () => {
				vi.mocked(auth.login).mockResolvedValueOnce(undefined);
				render(LoginForm);
				await openVerifyStep();

				await page.getByLabelText('Word 3').fill('charlie');
				await page.getByLabelText('Word 7').fill('gulf');
				await page.getByLabelText('Word 11').fill('kilo');
				await page.getByRole('button', { name: 'Enter workspace' }).click();

				await expect
					.element(page.getByText("Those words don't match your phrase", { exact: false }))
					.toBeInTheDocument();
				expect(auth.login).not.toHaveBeenCalled();

				await page.getByLabelText('Word 7').fill('golf');
				await page.getByRole('button', { name: 'Enter workspace' }).click();

				expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, undefined, undefined);
			});

			it('accepts words regardless of case and surrounding whitespace', async () => {
				vi.mocked(auth.login).mockResolvedValueOnce(undefined);
				render(LoginForm);
				await openVerifyStep();

				await page.getByLabelText('Word 3').fill('  Charlie ');
				await page.getByLabelText('Word 7').fill('GOLF');
				await page.getByLabelText('Word 11').fill('kilo');
				await page.getByRole('button', { name: 'Enter workspace' }).click();

				expect(auth.login).toHaveBeenCalledWith(STUB_PHRASE, undefined, undefined);
			});

			it('lets the user go back to re-read the phrase, then quizzes again', async () => {
				render(LoginForm);
				await openVerifyStep();

				await page.getByRole('button', { name: 'Show my phrase again' }).click();

				// The full phrase is visible again…
				await expect.element(page.getByText('charlie', { exact: true })).toBeInTheDocument();
				// …and continuing re-enters the quiz with empty fields.
				await page.getByRole('button', { name: 'Continue' }).click();
				await expect.element(page.getByLabelText('Word 3')).toHaveValue('');
				expect(auth.login).not.toHaveBeenCalled();
			});
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

		it('reveals a setup-token field on the generated-phrase verify screen too', async () => {
			vi.mocked(auth.login).mockRejectedValueOnce(bootstrapError).mockResolvedValueOnce(undefined);
			render(LoginForm);

			await page.getByRole('button', { name: 'Generate a recovery phrase' }).click();
			await acknowledgeAndVerify();

			const tokenInput = page.getByLabelText('Setup token');
			await expect.element(tokenInput).toBeInTheDocument();

			await tokenInput.fill('correct-token');
			await page.getByRole('button', { name: 'Enter workspace' }).click();

			expect(auth.login).toHaveBeenLastCalledWith(STUB_PHRASE, undefined, 'correct-token');
		});
	});
});
