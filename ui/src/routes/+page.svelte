<script lang="ts">
	import { auth } from '$lib/api/auth.svelte';
	import { channels, type Channel } from '$lib/api/channels';

	let mnemonic = $state('');
	let loginError = $state<string | null>(null);
	let loggingIn = $state(false);

	let channelList = $state<Channel[]>([]);
	let newChannelName = $state('');
	let loadError = $state<string | null>(null);

	async function loadChannels() {
		loadError = null;
		try {
			channelList = await channels.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load channels';
		}
	}

	async function handleLogin(event: SubmitEvent) {
		event.preventDefault();
		loginError = null;
		loggingIn = true;
		try {
			await auth.login(mnemonic.trim());
			mnemonic = '';
			await loadChannels();
		} catch (err) {
			loginError = err instanceof Error ? err.message : 'Login failed';
		} finally {
			loggingIn = false;
		}
	}

	async function handleCreateChannel(event: SubmitEvent) {
		event.preventDefault();
		if (!newChannelName.trim()) return;
		await channels.create(newChannelName.trim());
		newChannelName = '';
		await loadChannels();
	}
</script>

<main class="mx-auto flex min-h-screen max-w-2xl flex-col gap-8 px-6 py-16">
	<header>
		<h1 class="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
			Agent Hive
		</h1>
		<p class="mt-1 text-zinc-500 dark:text-zinc-400">
			A local-first workspace for humans and AI agent teams.
		</p>
	</header>

	{#if !auth.isAuthenticated}
		<form onsubmit={handleLogin} class="flex flex-col gap-3">
			<label class="text-sm font-medium text-zinc-700 dark:text-zinc-300" for="mnemonic">
				Workspace recovery phrase (12 words)
			</label>
			<input
				id="mnemonic"
				type="text"
				bind:value={mnemonic}
				placeholder="apple banana cherry ..."
				class="rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
			/>
			<button
				type="submit"
				disabled={loggingIn || !mnemonic.trim()}
				class="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
			>
				{loggingIn ? 'Signing in…' : 'Enter workspace'}
			</button>
			{#if loginError}
				<p class="text-sm text-red-600 dark:text-red-400">{loginError}</p>
			{/if}
			<p class="text-xs text-zinc-500">
				No workspace yet? Any valid 12-word phrase creates one on first login (US-001).
			</p>
		</form>
	{:else}
		<section class="flex flex-col gap-4">
			<div class="flex items-center justify-between">
				<h2 class="text-lg font-medium text-zinc-900 dark:text-zinc-50">Channels</h2>
				<button
					onclick={() => auth.logout()}
					class="text-sm text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
				>
					Sign out
				</button>
			</div>

			<form onsubmit={handleCreateChannel} class="flex gap-2">
				<input
					type="text"
					bind:value={newChannelName}
					placeholder="new-channel"
					class="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
				/>
				<button
					type="submit"
					class="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
				>
					Create
				</button>
			</form>

			{#if loadError}
				<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
			{:else if channelList.length === 0}
				<p class="text-sm text-zinc-500">No channels yet — create one above.</p>
			{:else}
				<ul
					class="divide-y divide-zinc-200 rounded-md border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800"
				>
					{#each channelList as channel (channel.id)}
						<li class="px-4 py-3">
							<p class="font-medium text-zinc-900 dark:text-zinc-50">#{channel.name}</p>
							{#if channel.description}
								<p class="text-sm text-zinc-500">{channel.description}</p>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/if}
</main>
