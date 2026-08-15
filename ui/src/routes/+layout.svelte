<script lang="ts">
	import './layout.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { auth } from '$lib/api/auth.svelte';
	import IdentityPicker from '$lib/components/IdentityPicker.svelte';
	import LoginForm from '$lib/components/LoginForm.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';

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
</script>

{#if isInviteRoute}
	{@render children()}
{:else if resuming}
	<!-- Blank-ish while the silent resume is in flight, rather than
	     flashing the login form at someone who's about to be let in. -->
	<main class="flex min-h-screen items-center justify-center">
		<p class="text-sm text-neutral-500">Signing you back in…</p>
	</main>
{:else if !auth.isAuthenticated}
	<LoginForm sessionExpired={auth.sessionExpired} />
{:else if !auth.humanId}
	<IdentityPicker />
{:else}
	<div class="flex h-screen">
		<Sidebar />
		<div class="flex-1 overflow-y-auto">
			{@render children()}
		</div>
	</div>
{/if}
