<script lang="ts">
	import './layout.css';
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
</script>

{#if isInviteRoute}
	{@render children()}
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
