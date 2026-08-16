<script lang="ts">
	// #15: owner-only invite management. The server-side OwnerGrant check on
	// POST/GET/DELETE /invites is what actually enforces it; the shell hides
	// the nav for non-owner sessions, and since #351 a non-owner who routes
	// here directly gets the <OwnerOnly> empty state instead of a page
	// whose every call 403s.
	import { auth } from '$lib/api/auth.svelte';
	import { invites, type Invite, type InviteCreated } from '$lib/api/invites';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import OwnerOnly from '$lib/components/OwnerOnly.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Invites (06-screens.md → Invites, mockup 2n): Create invite, a
	// one-time full-width copy panel, then the active list with Revoke.

	let invitesList = $state<Invite[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let creating = $state(false);
	let displayNameHint = $state('');
	let maxUses = $state(1);
	let expiresInHours = $state(168);
	let createBusy = $state(false);
	let createError = $state<string | null>(null);
	let created = $state<InviteCreated | null>(null);
	let copied = $state(false);
	let lanCopied = $state(false);

	let revoking = $state<Invite | null>(null);
	let rowError = $state<string | null>(null);

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';

	async function refresh() {
		loadError = null;
		try {
			invitesList = await invites.list();
		} catch {
			loadError = "Couldn't load invites.";
		} finally {
			loading = false;
		}
	}

	if (auth.grant === 'owner') refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		created = null;
		copied = false;
		lanCopied = false;
		createBusy = true;
		try {
			created = await invites.create({
				displayNameHint: displayNameHint.trim() || undefined,
				maxUses,
				expiresInHours
			});
			displayNameHint = '';
			creating = false;
			await refresh();
		} catch {
			createError = "Couldn't create the invite. Try again.";
		} finally {
			createBusy = false;
		}
	}

	async function handleCopy() {
		if (!created) return;
		await navigator.clipboard.writeText(created.url);
		copied = true;
	}

	async function handleCopyLanUrl() {
		if (!created?.lan_url) return;
		await navigator.clipboard.writeText(created.lan_url);
		lanCopied = true;
	}

	async function handleRevoke() {
		if (!revoking) return;
		rowError = null;
		try {
			await invites.revoke(revoking.id);
			revoking = null;
			await refresh();
		} catch {
			rowError = "Couldn't revoke that invite. Try again.";
			revoking = null;
		}
	}

	function isExpired(invite: Invite): boolean {
		return new Date(invite.expires_at).getTime() < Date.now();
	}

	function inviteStatus(invite: Invite): string {
		if (invite.revoked) return 'Revoked';
		if (invite.use_count >= invite.max_uses) return 'Used up';
		if (isExpired(invite)) return 'Expired';
		const usesLeft = invite.max_uses - invite.use_count;
		const days = Math.max(
			0,
			Math.round((new Date(invite.expires_at).getTime() - Date.now()) / 86_400_000)
		);
		return `${usesLeft} use${usesLeft === 1 ? '' : 's'} left · expires in ${days} day${days === 1 ? '' : 's'}`;
	}

	function isActive(invite: Invite): boolean {
		return !invite.revoked && invite.use_count < invite.max_uses && !isExpired(invite);
	}
</script>

{#if auth.grant !== 'owner'}
	<OwnerOnly title="Invites" />
{:else}
	<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
		<div class="mb-6 flex items-center justify-between gap-4">
			<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Invites</h1>
			<Button onclick={() => (creating = true)}>Create invite</Button>
		</div>

		{#if created}
			<div
				class="mb-6 rounded-2xl border border-accent bg-accent-soft px-6 py-5 dark:border-accent-dark dark:bg-accent-soft-dark"
			>
				<p class="mb-2.5 text-[15px] font-semibold text-ink dark:text-ink-dark">
					Save this now — it won't be shown again.
				</p>
				<div class="flex gap-2.5">
					<div
						class="flex h-12 min-w-0 flex-1 items-center overflow-hidden rounded-lg border border-line bg-surface px-4 dark:border-line-dark dark:bg-surface-dark"
					>
						<code class="truncate font-mono text-[13px] text-ink dark:text-ink-dark">
							{created.url}
						</code>
					</div>
					<Button class="flex-none" onclick={handleCopy}>{copied ? 'Copied' : 'Copy link'}</Button>
				</div>
				{#if created.loopback_only}
					<p class="mt-3 text-[13px] leading-normal text-warn-ink dark:text-warn-ink-dark">
						This link only works on this machine.
						{#if created.lan_url}
							If the other person is on the same network and you've deliberately opened this port
							beyond this machine, the link below can work instead. It won't work over the public
							internet.
						{:else}
							A device elsewhere can't reach it.
						{/if}
					</p>
				{/if}
				{#if created.lan_url}
					<div class="mt-2.5 flex gap-2.5">
						<div
							class="flex h-12 min-w-0 flex-1 items-center overflow-hidden rounded-lg border border-line bg-surface px-4 dark:border-line-dark dark:bg-surface-dark"
						>
							<code class="truncate font-mono text-[13px] text-ink dark:text-ink-dark">
								{created.lan_url}
							</code>
						</div>
						<Button variant="secondary" class="flex-none" onclick={handleCopyLanUrl}>
							{lanCopied ? 'Copied' : 'Copy'}
						</Button>
					</div>
				{/if}
			</div>
		{/if}

		{#if rowError}
			<p class="mb-3 text-sm text-danger">{rowError}</p>
		{/if}

		{#if loading}
			<SkeletonCards count={1} />
		{:else if loadError}
			<ErrorBanner message={loadError} onRetry={refresh} />
		{:else}
			<SectionLabel class="mb-2.5">Active invites</SectionLabel>
			{#if invitesList.filter(isActive).length === 0}
				<p class="py-4 text-base text-muted dark:text-muted-dark">
					No active invites. An invite lets someone join without your recovery phrase.
				</p>
			{:else}
				<div class="mb-8 flex flex-col gap-2">
					{#each invitesList.filter(isActive) as invite (invite.id)}
						<div
							class="flex min-h-16 items-center gap-3 rounded-xl border border-line bg-surface px-4.5 dark:border-line-dark dark:bg-surface-dark"
						>
							<span class="text-base font-semibold text-ink dark:text-ink-dark">
								{invite.display_name_hint ?? 'Anyone'}
							</span>
							<span class="text-sm text-muted dark:text-muted-dark">{inviteStatus(invite)}</span>
							<button
								type="button"
								onclick={() => (revoking = invite)}
								class="ml-auto text-sm font-medium text-danger hover:underline"
							>
								Revoke
							</button>
						</div>
					{/each}
				</div>
			{/if}
			{#if invitesList.some((i) => !isActive(i))}
				<SectionLabel class="mb-2.5">Past invites</SectionLabel>
				<div class="flex flex-col gap-2">
					{#each invitesList.filter((i) => !isActive(i)) as invite (invite.id)}
						<div
							class="flex min-h-12 items-center gap-3 rounded-xl border border-line bg-surface px-4.5 opacity-60 dark:border-line-dark dark:bg-surface-dark"
						>
							<span class="text-[15px] text-ink dark:text-ink-dark">
								{invite.display_name_hint ?? 'Anyone'}
							</span>
							<span class="ml-auto text-sm text-muted dark:text-muted-dark">
								{inviteStatus(invite)}
							</span>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	</div>
{/if}

{#if creating}
	<Sheet title="Create invite" onClose={() => (creating = false)} width={480}>
		<form id="new-invite-form" onsubmit={handleCreate} class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="invite-name">
					For (optional)
				</label>
				<input
					id="invite-name"
					type="text"
					bind:value={displayNameHint}
					placeholder="e.g. Ada"
					class={inputClass}
				/>
			</div>
			<div class="grid grid-cols-2 gap-4">
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="invite-uses">
						Uses
					</label>
					<input id="invite-uses" type="number" min="1" bind:value={maxUses} class={inputClass} />
				</div>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="invite-expiry">
						Expires in (hours)
					</label>
					<input
						id="invite-expiry"
						type="number"
						min="1"
						bind:value={expiresInHours}
						class={inputClass}
					/>
				</div>
			</div>
			<p class="text-[13px] leading-normal text-muted dark:text-muted-dark">
				An invite link only works if this machine is reachable from the other person's device — the
				same machine, or the same network if you've deliberately opened it up.
			</p>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy}
				onclick={() =>
					(document.getElementById('new-invite-form') as HTMLFormElement).requestSubmit()}
			>
				{createBusy ? 'Creating…' : 'Create invite'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if revoking}
	<Sheet title="Revoke this invite?" onClose={() => (revoking = null)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			{revoking.display_name_hint
				? `${revoking.display_name_hint} won't be able to join with it, and anyone who already joined through it loses their way back in.`
				: 'The link stops working, and anyone who already joined through it loses their way back in.'}
		</p>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (revoking = null)}>Cancel</Button>
			<Button variant="destructive" onclick={handleRevoke}>Revoke invite</Button>
		{/snippet}
	</Sheet>
{/if}
