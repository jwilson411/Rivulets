<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { mcpServers, type MCPServerDetail } from '$lib/api/mcpServers';

	let serverList = $state<MCPServerDetail[]>([]);
	let loadError = $state<string | null>(null);

	let name = $state('');
	let url = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let rowError = $state<string | null>(null);
	let reconnectingId = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			// The list endpoint doesn't include tools, so fetch details for
			// each server to show its discovered tool count inline.
			const servers = await mcpServers.list();
			serverList = await Promise.all(servers.map((s) => mcpServers.get(s.id)));
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load MCP servers';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (!name.trim() || !url.trim()) return;
		creating = true;
		try {
			await mcpServers.create({ name: name.trim(), url: url.trim() });
			name = '';
			url = '';
			await refresh();
		} catch (err) {
			createError = err instanceof ApiError ? err.message : 'Failed to register MCP server';
		} finally {
			creating = false;
		}
	}

	async function handleReconnect(id: string) {
		rowError = null;
		reconnectingId = id;
		try {
			await mcpServers.reconnect(id);
			await refresh();
		} catch (err) {
			rowError = err instanceof ApiError ? err.message : 'Failed to reconnect';
		} finally {
			reconnectingId = null;
		}
	}

	async function handleDelete(id: string) {
		rowError = null;
		try {
			await mcpServers.remove(id);
			await refresh();
		} catch (err) {
			rowError = err instanceof ApiError ? err.message : 'Failed to remove MCP server';
		}
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">MCP Servers</h1>
		<p class="text-sm text-zinc-500">
			Connect external MCP servers to discover tools agents can use (FR-8.5). A server that fails to
			connect is still saved — reconnect to retry.
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-md border border-zinc-200 p-4 dark:border-zinc-800"
	>
		<h2 class="text-sm font-medium text-zinc-700 dark:text-zinc-300">Register server</h2>
		<input
			type="text"
			bind:value={name}
			placeholder="Name (e.g. Filesystem tools)"
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<input
			type="text"
			bind:value={url}
			placeholder="URL (streamable-http endpoint)"
			class="rounded-md border border-zinc-300 px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={creating}
			class="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			{creating ? 'Registering…' : 'Register server'}
		</button>
		{#if createError}
			<p class="text-sm text-red-600 dark:text-red-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if serverList.length === 0}
		<p class="text-sm text-zinc-400">No MCP servers registered yet — add one above.</p>
	{:else}
		{#if rowError}
			<p class="text-sm text-red-600 dark:text-red-400">{rowError}</p>
		{/if}
		<ul class="flex flex-col gap-2">
			{#each serverList as server (server.id)}
				<li class="rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800">
					<div class="flex items-center justify-between">
						<div>
							<p class="font-medium text-zinc-900 dark:text-zinc-50">
								{server.name}
								<span
									class="ml-2 rounded-full px-2 py-0.5 text-xs {server.connected
										? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400'
										: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400'}"
								>
									{server.connected ? 'connected' : 'disconnected'}
								</span>
							</p>
							<p class="text-xs text-zinc-400">{server.url}</p>
							{#if server.connected}
								<p class="mt-1 text-xs text-zinc-500">
									{server.tools.length} tool{server.tools.length === 1 ? '' : 's'}:
									{server.tools.map((t) => t.name).join(', ')}
								</p>
							{/if}
						</div>
						<div class="flex shrink-0 items-center gap-3">
							<button
								onclick={() => handleReconnect(server.id)}
								disabled={reconnectingId === server.id}
								class="text-xs text-zinc-500 hover:text-zinc-900 disabled:opacity-50 dark:text-zinc-400 dark:hover:text-zinc-50"
							>
								{reconnectingId === server.id ? 'Reconnecting…' : 'Reconnect'}
							</button>
							<button
								onclick={() => handleDelete(server.id)}
								class="text-xs text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
							>
								Remove
							</button>
						</div>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
