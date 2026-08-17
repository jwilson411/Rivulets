<script lang="ts">
	import './layout.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { auth } from '$lib/api/auth.svelte';
	import { writeLastChannel } from '$lib/lastChannel';
	import IdentityPicker from '$lib/components/IdentityPicker.svelte';
	import LoginForm from '$lib/components/LoginForm.svelte';
	import IconRail from '$lib/shell/IconRail.svelte';
	import ContextPanel from '$lib/shell/ContextPanel.svelte';
	import MobileTabs from '$lib/shell/MobileTabs.svelte';
	import MoreSheet from '$lib/shell/MoreSheet.svelte';
	import AccountMenu from '$lib/shell/AccountMenu.svelte';
	import CommandPalette from '$lib/shell/CommandPalette.svelte';

	let { children } = $props();

	// #15: accepting an invite must work before this browser has any
	// session at all, so it bypasses the normal auth gate entirely rather
	// than needing LoginForm/IdentityPicker to grow an invite-aware branch.
	let isInviteRoute = $derived(page.url.pathname.startsWith('/invite/'));

	// #350: an invite-redeemed browser holds a persisted resume credential
	// (the session JWT itself is memory-only, so it never survives a
	// reload) — exchange it silently on startup so a refresh lands the
	// invited human back in the workspace instead of on the mnemonic-only
	// LoginForm. Only on initial load: after an explicit sign-out, resuming
	// stays a deliberate click (LoginForm's "Continue as …"), not something
	// that silently undoes the sign-out.
	// Initialized synchronously, not in onMount — the first render must
	// already know a resume attempt is coming, or LoginForm flashes for a
	// frame before it starts.
	let resuming = $state(!auth.isAuthenticated && auth.resumeDisplayName !== null);
	onMount(() => {
		if (!resuming) return;
		auth
			.resumeInviteSession()
			.catch(() => {
				// Transient failure (locked node, network) — fall through to
				// LoginForm, where the kept credential is offered as a button
				// whose own error handling can actually be seen.
			})
			.finally(() => {
				resuming = false;
			});
	});

	let moreOpen = $state(false);
	let accountOpen = $state(false);
	let paletteOpen = $state(false);

	// The Hash rail icon reopens whatever channel was last visited.
	$effect(() => {
		const match = /^\/channels\/([^/]+)/.exec(page.url.pathname);
		if (match) writeLastChannel(match[1]);
	});

	function handleKeydown(event: KeyboardEvent) {
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
			event.preventDefault();
			paletteOpen = !paletteOpen;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if isInviteRoute}
	{@render children()}
{:else if resuming}
	<!-- Blank-ish while the silent resume is in flight, rather than
	     flashing the login form at someone who's about to be let in. -->
	<main class="flex min-h-screen items-center justify-center">
		<p class="text-sm text-muted dark:text-muted-dark">Signing you back in…</p>
	</main>
{:else if !auth.isAuthenticated}
	<LoginForm sessionExpired={auth.sessionExpired} />
{:else if !auth.humanId}
	<IdentityPicker />
{:else}
	<div class="flex h-screen flex-col">
		<div class="flex min-h-0 flex-1">
			<IconRail
				onOpenMore={() => (moreOpen = true)}
				onOpenAccount={() => (accountOpen = true)}
				onOpenPalette={() => (paletteOpen = true)}
			/>
			<ContextPanel onOpenPalette={() => (paletteOpen = true)} />
			<main class="min-w-0 flex-1 overflow-y-auto">
				{@render children()}
			</main>
		</div>
		<MobileTabs onOpenMore={() => (moreOpen = true)} />
	</div>

	{#if moreOpen}
		<MoreSheet
			onClose={() => (moreOpen = false)}
			onOpenPalette={() => {
				moreOpen = false;
				paletteOpen = true;
			}}
		/>
	{/if}
	{#if accountOpen}
		<AccountMenu onClose={() => (accountOpen = false)} />
	{/if}
	{#if paletteOpen}
		<CommandPalette onClose={() => (paletteOpen = false)} />
	{/if}
{/if}
