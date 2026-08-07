<script lang="ts">
	import './layout.css';
	import { auth } from '$lib/api/auth.svelte';
	import IdentityPicker from '$lib/components/IdentityPicker.svelte';
	import LoginForm from '$lib/components/LoginForm.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';

	let { children } = $props();
</script>

{#if !auth.isAuthenticated}
	<LoginForm />
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
