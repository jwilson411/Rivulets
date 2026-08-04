<script lang="ts">
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import { auth } from '$lib/api/auth.svelte';
	import LoginForm from '$lib/components/LoginForm.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';

	let { children } = $props();
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>

{#if !auth.isAuthenticated}
	<LoginForm />
{:else}
	<div class="flex h-screen">
		<Sidebar />
		<div class="flex-1 overflow-y-auto">
			{@render children()}
		</div>
	</div>
{/if}
