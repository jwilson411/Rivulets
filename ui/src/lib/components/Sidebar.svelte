<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { auth } from '$lib/api/auth.svelte';
	import { channels, type Channel } from '$lib/api/channels';

	let channelList = $state<Channel[]>([]);
	let newChannelName = $state('');
	let loadError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			channelList = await channels.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load channels';
		}
	}

	refresh();

	async function handleCreateChannel(event: SubmitEvent) {
		event.preventDefault();
		if (!newChannelName.trim()) return;
		await channels.create(newChannelName.trim());
		newChannelName = '';
		await refresh();
	}

	function isActive(path: string): boolean {
		return page.url.pathname === path || page.url.pathname.startsWith(path + '/');
	}
</script>

<nav
	class="flex h-full w-60 flex-col border-r border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950"
>
	<div class="px-4 py-4">
		<a href={resolve('/')} class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
			Agent Hive
		</a>
	</div>

	<div class="flex flex-col gap-0.5 px-2">
		<a
			href={resolve('/agents')}
			class="rounded-md px-2 py-1.5 text-sm {isActive('/agents')
				? 'bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
				: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'}"
		>
			Agents
		</a>
		<a
			href={resolve('/teams')}
			class="rounded-md px-2 py-1.5 text-sm {isActive('/teams')
				? 'bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
				: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'}"
		>
			Teams
		</a>
		<a
			href={resolve('/providers')}
			class="rounded-md px-2 py-1.5 text-sm {isActive('/providers')
				? 'bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
				: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'}"
		>
			Providers
		</a>
		<a
			href={resolve('/mcp-servers')}
			class="rounded-md px-2 py-1.5 text-sm {isActive('/mcp-servers')
				? 'bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
				: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'}"
		>
			MCP Servers
		</a>
	</div>

	<div class="mt-4 flex items-center justify-between px-4">
		<span class="text-xs font-semibold tracking-wide text-zinc-400 uppercase">Channels</span>
	</div>

	<div class="flex-1 overflow-y-auto px-2 py-1">
		{#if loadError}
			<p class="px-2 text-xs text-red-600 dark:text-red-400">{loadError}</p>
		{:else}
			{#each channelList as channel (channel.id)}
				<a
					href={resolve('/channels/[id]', { id: channel.id })}
					class="block truncate rounded-md px-2 py-1.5 text-sm {isActive('/channels/' + channel.id)
						? 'bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
						: 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900'}"
				>
					#{channel.name}
				</a>
			{/each}
		{/if}
	</div>

	<form
		onsubmit={handleCreateChannel}
		class="flex gap-1 border-t border-zinc-200 p-2 dark:border-zinc-800"
	>
		<input
			type="text"
			bind:value={newChannelName}
			placeholder="new-channel"
			class="min-w-0 flex-1 rounded-md border border-zinc-300 px-2 py-1 text-xs focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			class="rounded-md bg-zinc-900 px-2 py-1 text-xs font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
		>
			Add
		</button>
	</form>

	<div class="border-t border-zinc-200 p-2 dark:border-zinc-800">
		<button
			onclick={() => auth.logout()}
			class="w-full rounded-md px-2 py-1.5 text-left text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
		>
			Sign out
		</button>
	</div>
</nav>
