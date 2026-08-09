<script lang="ts">
	// #15: owner-only invite management. This page itself isn't gated by
	// role (Sidebar just hides the nav link for non-owner sessions) --
	// the server-side OwnerGrant check on POST/GET/DELETE /invites is what
	// actually enforces it, this page just won't have anything to show an
	// invite-grant session since every call here 403s for one.
	import { ApiError } from '$lib/api/client';
	import { invites, type Invite, type InviteCreated } from '$lib/api/invites';

	let invitesList = $state<Invite[]>([]);
	let loadError = $state<string | null>(null);

	let displayNameHint = $state('');
	let maxUses = $state(1);
	let expiresInHours = $state(168);
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let created = $state<InviteCreated | null>(null);
	let copied = $state(false);
	let lanCopied = $state(false);

	let rowError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			invitesList = await invites.list();
		} catch (err) {
			loadError = err instanceof ApiError ? err.message : 'Failed to load invites';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		created = null;
		copied = false;
		lanCopied = false;
		creating = true;
		try {
			created = await invites.create({
				displayNameHint: displayNameHint.trim() || undefined,
				maxUses,
				expiresInHours
			});
			displayNameHint = '';
			await refresh();
		} catch (err) {
			createError = err instanceof ApiError ? err.message : 'Failed to create invite';
		} finally {
			creating = false;
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

	async function handleRevoke(invite: Invite) {
		rowError = null;
		try {
			await invites.revoke(invite.id);
			await refresh();
		} catch (err) {
			rowError = err instanceof ApiError ? err.message : 'Failed to revoke invite';
		}
	}

	function isExpired(invite: Invite): boolean {
		return new Date(invite.expires_at).getTime() < Date.now();
	}

	function statusLabel(invite: Invite): string {
		if (invite.revoked) return 'revoked';
		if (invite.use_count >= invite.max_uses) return 'used up';
		if (isExpired(invite)) return 'expired';
		return 'active';
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Invites</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Let a second human join this workspace without sharing your recovery phrase (#15). An invite
			link only works if this node is reachable from their device — by default that means the same
			machine or, with Tailscale/LAN exposure configured, the same network.
		</p>
	</header>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{/if}

	<section
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Create an invite</h2>
		<form onsubmit={handleCreate} class="flex flex-wrap items-end gap-2">
			<div class="flex flex-col gap-1">
				<label class="text-xs text-neutral-500" for="display-name-hint">Name (optional)</label>
				<input
					id="display-name-hint"
					type="text"
					bind:value={displayNameHint}
					placeholder="e.g. Ada"
					class="min-w-0 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			</div>
			<div class="flex flex-col gap-1">
				<label class="text-xs text-neutral-500" for="max-uses">Max uses</label>
				<input
					id="max-uses"
					type="number"
					min="1"
					bind:value={maxUses}
					class="w-20 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			</div>
			<div class="flex flex-col gap-1">
				<label class="text-xs text-neutral-500" for="expires-in-hours">Expires in (hours)</label>
				<input
					id="expires-in-hours"
					type="number"
					min="1"
					bind:value={expiresInHours}
					class="w-24 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			</div>
			<button
				type="submit"
				disabled={creating}
				class="rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
			>
				{creating ? 'Creating…' : 'Create invite'}
			</button>
		</form>
		{#if createError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}

		{#if created}
			<div class="flex flex-col gap-2 rounded-md border border-ink/12 p-3 dark:border-white/10">
				<p class="text-xs text-neutral-500">
					This link is shown once — copy it now. Anyone with it can join as the name above (or pick
					their own) until it expires or is revoked.
				</p>
				<div class="flex items-center gap-2">
					<code class="min-w-0 flex-1 truncate text-xs text-ink dark:text-ink-dark"
						>{created.url}</code
					>
					<button
						type="button"
						onclick={handleCopy}
						class="shrink-0 rounded-md border border-ink/15 px-3 py-1 text-xs font-medium text-ink dark:border-white/15 dark:text-ink-dark"
					>
						{copied ? 'Copied' : 'Copy'}
					</button>
				</div>

				{#if created.loopback_only}
					<p
						class="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
					>
						This link only works on this machine (127.0.0.1) — a device elsewhere can't reach it.
						{#if created.lan_url}
							If this device and the invitee's are on the same local network <strong>and</strong>
							you've deliberately exposed this port beyond localhost (see docs/security.md), try the link
							below instead. It still won't work over the public internet.
						{:else}
							We couldn't detect a local network address to offer as an alternative.
						{/if}
					</p>
				{/if}

				{#if created.lan_url}
					<div class="flex items-center gap-2">
						<code class="min-w-0 flex-1 truncate text-xs text-ink dark:text-ink-dark"
							>{created.lan_url}</code
						>
						<button
							type="button"
							onclick={handleCopyLanUrl}
							class="shrink-0 rounded-md border border-ink/15 px-3 py-1 text-xs font-medium text-ink dark:border-white/15 dark:text-ink-dark"
						>
							{lanCopied ? 'Copied' : 'Copy LAN link'}
						</button>
					</div>
				{/if}
			</div>
		{/if}
	</section>

	<section class="flex flex-col gap-2">
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Outstanding invites</h2>
		{#if rowError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{rowError}</p>
		{/if}
		{#if invitesList.length === 0}
			<p class="text-sm text-neutral-500 italic">No invites yet.</p>
		{:else}
			{#each invitesList as invite (invite.id)}
				<div
					class="flex items-center justify-between gap-3 rounded-lg border border-ink/12 bg-surface p-3 text-sm dark:border-white/10 dark:bg-surface-dark"
				>
					<div class="flex flex-col">
						<span class="text-ink dark:text-ink-dark"
							>{invite.display_name_hint ?? '(no name hint)'}</span
						>
						<span class="text-xs text-neutral-500">
							{invite.use_count}/{invite.max_uses} used · {statusLabel(invite)}
						</span>
					</div>
					{#if !invite.revoked}
						<button
							type="button"
							onclick={() => handleRevoke(invite)}
							class="shrink-0 rounded-md border border-ink/15 px-3 py-1 text-xs font-medium text-agent-magenta-700 dark:border-white/15 dark:text-agent-magenta-400"
						>
							Revoke
						</button>
					{/if}
				</div>
			{/each}
		{/if}
	</section>
</div>
