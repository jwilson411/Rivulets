<script lang="ts">
	// Unlock screen (06-screens.md → Unlock, mockups 1a/1b). #136: offers to
	// generate a fresh BIP-39 phrase client-side, rather than adding a
	// server endpoint for it -- the phrase is the workspace's only
	// credential (security/keys.py's generate_mnemonic), so it should exist
	// in the browser and nowhere else until the user has seen and
	// acknowledged it. auth.login is what finally sends it over the wire,
	// same as a manually-entered phrase.
	//
	// The generated-phrase warning card is deliberately the ONE loud
	// severity moment in the whole app (HANDOFF.md invariant 6).
	import { generateMnemonic } from 'bip39';
	import { auth } from '$lib/api/auth.svelte';
	import { ApiError } from '$lib/api/client';
	import { initials } from '$lib/ink';
	import { isUnlockPhraseReady } from '$lib/mnemonic';
	import Icon from '$lib/ui/Icon.svelte';

	// Set by +layout.svelte's auth gate when it swaps back to LoginForm
	// because a previously-valid session was torn down (401 / JWT expiry),
	// rather than the user simply never having logged in.
	let { sessionExpired = false }: { sessionExpired?: boolean } = $props();

	type View = 'landing' | 'enter' | 'generated';
	let view = $state<View>('landing');

	let mnemonic = $state('');
	let passphrase = $state('');
	let showPassphrase = $state(false);
	let loginError = $state<string | null>(null);
	let loggingIn = $state(false);

	let generatedWords = $state<string[] | null>(null);
	let generatedPassphrase = $state('');
	let showGeneratedPassphrase = $state(false);
	let acknowledged = $state(false);
	let copied = $state(false);

	// Only shown once a login attempt has actually hit the server's #247
	// bootstrap-token gate (api/auth.py) -- there's no way to know in
	// advance whether this node is bound to 0.0.0.0 and unclaimed, so the
	// field stays hidden until the server's 401 says it's needed, rather
	// than cluttering every login with an "advanced" field almost nobody
	// on a loopback-bound node will ever use.
	let bootstrapToken = $state('');
	let needsBootstrapToken = $state(false);

	// #407: opt-in stay-signed-in. Off by default — storing the phrase on
	// this machine is a real tradeoff, so it has to be a deliberate check.
	// If this browser already holds a stay credential (JWT expired on an
	// open tab, or Unlock after a failed silent resume), start checked so
	// unlocking again doesn't silently drop the preference.
	let staySignedIn = $state(auth.ownerStayEnabled);

	// #350: an invite-redeemed browser holds a persisted resume credential
	// and no mnemonic — after a sign-out or an expired session, this button
	// is its way back in. auth.resumeDisplayName goes null (and the offer
	// disappears) if the server rejects the credential as dead.
	let resumeError = $state<string | null>(null);
	let resumingInvite = $state(false);

	async function handleResume() {
		resumeError = null;
		resumingInvite = true;
		try {
			if (!(await auth.resumeInviteSession())) {
				resumeError =
					'Your invite access is no longer valid — ask the workspace owner for a new invite link.';
			}
		} catch (err) {
			resumeError = err instanceof Error ? err.message : 'Could not sign you back in';
		} finally {
			resumingInvite = false;
		}
	}

	async function performLogin(phrase: string, phrasePassphrase: string): Promise<boolean> {
		loginError = null;
		loggingIn = true;
		try {
			const trimmed = phrase.trim();
			await auth.login(trimmed, phrasePassphrase || undefined, bootstrapToken || undefined);
			// Persist or drop the stay credential only after a successful
			// login, so a failed attempt doesn't lock in (or wipe) anything.
			if (staySignedIn) {
				auth.rememberOwnerStay(trimmed, phrasePassphrase || undefined);
			} else {
				auth.forgetOwnerStay();
			}
			return true;
		} catch (err) {
			loginError = err instanceof Error ? err.message : 'Login failed';
			if (
				err instanceof ApiError &&
				err.status === 401 &&
				err.message.includes('RIVULETS_BOOTSTRAP_TOKEN')
			) {
				needsBootstrapToken = true;
			}
			return false;
		} finally {
			loggingIn = false;
		}
	}

	const canSubmitPhrase = $derived(!loggingIn && isUnlockPhraseReady(mnemonic));

	async function handleLogin(event: SubmitEvent) {
		event.preventDefault();
		if (!isUnlockPhraseReady(mnemonic)) return;
		if (await performLogin(mnemonic, passphrase)) {
			mnemonic = '';
			passphrase = '';
			bootstrapToken = '';
			needsBootstrapToken = false;
		}
	}

	function generatePhrase() {
		// 128 bits of entropy -> 12 words, matching the server's
		// _MNEMONIC_STRENGTH_BITS (security/keys.py).
		generatedWords = generateMnemonic(128).split(' ');
		generatedPassphrase = '';
		showGeneratedPassphrase = false;
		acknowledged = false;
		copied = false;
		loginError = null;
		view = 'generated';
	}

	function backToLanding() {
		generatedWords = null;
		generatedPassphrase = '';
		acknowledged = false;
		copied = false;
		loginError = null;
		view = 'landing';
	}

	async function copyGeneratedPhrase() {
		if (!generatedWords) return;
		await navigator.clipboard.writeText(generatedWords.join(' '));
		copied = true;
	}

	async function confirmGeneratedPhrase() {
		if (!generatedWords || !acknowledged) return;
		if (await performLogin(generatedWords.join(' '), generatedPassphrase)) {
			generatedWords = null;
			generatedPassphrase = '';
			acknowledged = false;
			bootstrapToken = '';
			needsBootstrapToken = false;
		}
	}

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';
</script>

{#snippet bootstrapField(id: string)}
	{#if needsBootstrapToken}
		<div class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for={id}>
				Setup token
			</label>
			<input
				{id}
				type="password"
				autocomplete="off"
				bind:value={bootstrapToken}
				class={inputClass}
			/>
			<p class="text-[13px] text-muted dark:text-muted-dark">
				This machine is reachable on the network. Paste the setup token from the person who
				installed it.
			</p>
		</div>
	{/if}
{/snippet}

{#snippet staySignedInField()}
	<label class="flex cursor-pointer items-start gap-3">
		<input type="checkbox" bind:checked={staySignedIn} class="peer sr-only" />
		<span
			class="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-[8px] border border-line bg-surface text-transparent peer-checked:border-accent peer-checked:bg-accent peer-checked:text-white peer-focus-visible:outline-2 peer-focus-visible:outline-accent dark:border-line-dark dark:bg-surface-dark dark:peer-checked:border-accent-dark dark:peer-checked:bg-accent-dark dark:peer-checked:text-paper-dark"
			aria-hidden="true"
		>
			<Icon name="check" class="h-3.5 w-3.5" />
		</span>
		<span class="flex flex-col gap-1">
			<span class="text-[15px] text-ink dark:text-ink-dark">Stay signed in on this machine</span>
			<span class="text-[13px] text-muted dark:text-muted-dark">
				{#if staySignedIn}
					Stores your recovery phrase in this browser so refresh and new tabs keep you signed in.
					Anyone with access to this browser can unlock the workspace. Sign out to remove it.
				{:else}
					Refreshing or opening a new tab will sign you out.
				{/if}
			</span>
		</span>
	</label>
{/snippet}

<main class="mx-auto flex min-h-screen w-full max-w-[480px] flex-col justify-center px-6 py-12">
	<div class="mb-10 flex items-center gap-3">
		<span
			class="flex h-9 w-9 items-center justify-center rounded-[12px] bg-accent text-white dark:bg-accent-dark dark:text-paper-dark"
		>
			<Icon name="logo" class="h-5 w-5" />
		</span>
		<span class="font-display text-[19px] font-semibold text-ink dark:text-ink-dark">Rivulets</span>
	</div>

	{#if sessionExpired}
		<p class="mb-6 text-[15px] text-muted dark:text-muted-dark">
			Your session ended — unlock again.
		</p>
	{/if}

	{#if view === 'generated' && generatedWords}
		<div
			class="mb-7 rounded-2xl border border-danger-line bg-danger-soft px-6 py-5 dark:border-danger-line-dark dark:bg-danger-soft-dark"
		>
			<p class="mb-1.5 text-base font-semibold text-danger">This phrase is the only way back in.</p>
			<p class="text-sm leading-normal text-danger-ink dark:text-danger-ink-dark">
				There is no password reset. Anyone with this phrase can open the workspace. If you lose it,
				the workspace is gone. Write it down or save it in a password manager before you continue.
			</p>
		</div>

		<div class="mb-5 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
			{#each generatedWords as word, i (i)}
				<div
					class="flex items-baseline gap-2 rounded-lg border border-line bg-surface px-3 py-2.5 dark:border-line-dark dark:bg-surface-dark"
				>
					<span class="font-mono text-[11px] text-muted dark:text-muted-dark">
						{String(i + 1).padStart(2, '0')}
					</span>
					<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">{word}</span>
				</div>
			{/each}
		</div>

		<button
			type="button"
			onclick={copyGeneratedPhrase}
			class="mb-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-line bg-surface text-base font-medium text-ink hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:hover:border-accent-dark"
		>
			<Icon name="copy" class="h-[18px] w-[18px]" />
			{copied ? 'Copied' : 'Copy phrase'}
		</button>

		{#if showGeneratedPassphrase}
			<div class="mb-6 flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="generated-passphrase">
					Passphrase
				</label>
				<input
					id="generated-passphrase"
					type="password"
					autocomplete="off"
					bind:value={generatedPassphrase}
					class={inputClass}
				/>
				<p class="text-[13px] text-muted dark:text-muted-dark">
					Optional. You'll need this every time, along with the phrase. There is no reset.
				</p>
			</div>
		{/if}

		{@render bootstrapField('generated-bootstrap-token')}

		<div class="mb-6">
			{@render staySignedInField()}
		</div>

		<label class="mb-6 flex cursor-pointer items-center gap-3">
			<input type="checkbox" bind:checked={acknowledged} class="peer sr-only" />
			<span
				class="flex h-6 w-6 flex-none items-center justify-center rounded-[8px] border border-line bg-surface text-transparent peer-checked:border-accent peer-checked:bg-accent peer-checked:text-white peer-focus-visible:outline-2 peer-focus-visible:outline-accent dark:border-line-dark dark:bg-surface-dark dark:peer-checked:border-accent-dark dark:peer-checked:bg-accent-dark dark:peer-checked:text-paper-dark"
				aria-hidden="true"
			>
				<Icon name="check" class="h-3.5 w-3.5" />
			</span>
			<span class="text-[15px] text-ink dark:text-ink-dark">
				I've saved this phrase somewhere safe
			</span>
		</label>

		<button
			type="button"
			disabled={loggingIn || !acknowledged}
			onclick={confirmGeneratedPhrase}
			class="flex h-14 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-white transition-colors hover:bg-accent-deep disabled:opacity-40 dark:bg-accent-dark dark:text-paper-dark"
		>
			{loggingIn ? 'Unlocking…' : 'Enter workspace'}
		</button>

		{#if loginError}
			<p class="mt-4 text-sm text-danger">{loginError}</p>
		{/if}

		<div class="mt-4 flex items-center justify-center gap-6">
			{#if !showGeneratedPassphrase}
				<button
					type="button"
					onclick={() => (showGeneratedPassphrase = true)}
					class="text-sm font-medium text-accent hover:underline dark:text-accent-dark"
				>
					Add a passphrase
				</button>
			{/if}
			<button
				type="button"
				onclick={backToLanding}
				class="text-sm text-muted hover:underline dark:text-muted-dark"
			>
				Back
			</button>
		</div>
	{:else if view === 'enter'}
		<h1
			class="mb-3 font-display text-[32px] leading-tight font-semibold text-ink dark:text-ink-dark"
		>
			Your workspace lives on this machine.
		</h1>
		<p class="mb-8 text-base text-muted dark:text-muted-dark">
			No account. A 12-word phrase is the only key.
		</p>
		<form onsubmit={handleLogin} class="flex flex-col gap-5">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="mnemonic">
					Workspace recovery phrase
				</label>
				<textarea
					id="mnemonic"
					rows="3"
					bind:value={mnemonic}
					placeholder="twelve words, separated by spaces"
					class="rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[15px] leading-relaxed text-ink placeholder:font-sans placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
				></textarea>
			</div>
			{#if showPassphrase}
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="passphrase">
						Passphrase
					</label>
					<input
						id="passphrase"
						type="password"
						autocomplete="off"
						bind:value={passphrase}
						class={inputClass}
					/>
					<p class="text-[13px] text-muted dark:text-muted-dark">
						Optional. You'll need this every time, along with the phrase. There is no reset.
					</p>
				</div>
			{/if}
			{@render bootstrapField('bootstrap-token')}
			{@render staySignedInField()}
			<button
				type="submit"
				disabled={!canSubmitPhrase}
				class="flex h-14 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-white transition-colors hover:bg-accent-deep disabled:opacity-40 dark:bg-accent-dark dark:text-paper-dark"
			>
				{loggingIn ? 'Unlocking…' : 'Enter workspace'}
			</button>
			{#if loginError}
				<p class="text-sm text-danger">{loginError}</p>
			{/if}
			<div class="flex items-center justify-center gap-6">
				{#if !showPassphrase}
					<button
						type="button"
						onclick={() => (showPassphrase = true)}
						class="text-sm font-medium text-accent hover:underline dark:text-accent-dark"
					>
						Add a passphrase
					</button>
				{/if}
				<button
					type="button"
					onclick={() => (view = 'landing')}
					class="text-sm text-muted hover:underline dark:text-muted-dark"
				>
					Back
				</button>
			</div>
		</form>
	{:else}
		<h1
			class="mb-3 font-display text-[32px] leading-tight font-semibold text-ink dark:text-ink-dark"
		>
			Your workspace lives on this machine.
		</h1>
		<p class="mb-9 text-base text-muted dark:text-muted-dark">
			No account. A 12-word phrase is the only key.
		</p>

		<button
			type="button"
			onclick={generatePhrase}
			class="mb-4 flex h-14 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-white transition-colors hover:bg-accent-deep dark:bg-accent-dark dark:text-paper-dark"
		>
			Generate a recovery phrase
		</button>
		<button
			type="button"
			onclick={() => (view = 'enter')}
			class="flex h-12 w-full items-center justify-center rounded-xl border border-line bg-surface text-base font-medium text-ink hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:hover:border-accent-dark"
		>
			I already have a phrase
		</button>

		{#if auth.resumeDisplayName !== null || resumeError}
			<div class="mt-7 mb-5 flex items-center gap-3">
				<span class="h-px flex-1 bg-line dark:bg-line-dark"></span>
				<span class="text-[13px] text-muted dark:text-muted-dark">or</span>
				<span class="h-px flex-1 bg-line dark:bg-line-dark"></span>
			</div>
			{#if auth.resumeDisplayName !== null}
				<button
					type="button"
					disabled={resumingInvite}
					onclick={handleResume}
					class="flex h-12 w-full items-center gap-3 rounded-xl border border-line bg-surface px-4 hover:border-accent disabled:opacity-50 dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<span
						class="flex h-7 w-7 flex-none items-center justify-center rounded-[10px] bg-ink text-[13px] font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark"
					>
						{initials(auth.resumeDisplayName)}
					</span>
					<span class="text-base font-medium text-ink dark:text-ink-dark">
						{resumingInvite ? 'Unlocking…' : `Continue as ${auth.resumeDisplayName}`}
					</span>
				</button>
			{/if}
			{#if resumeError}
				<p class="mt-3 text-sm text-danger">{resumeError}</p>
			{/if}
		{/if}
	{/if}
</main>
