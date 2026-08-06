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
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">MCP Servers</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Connect external MCP servers to discover tools agents can use (FR-8.5). A server that fails to
			connect is still saved — reconnect to retry.
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Register server</h2>
		<input
			type="text"
			bind:value={name}
			placeholder="Name (e.g. Filesystem tools)"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<input
			type="text"
			bind:value={url}
			placeholder="URL (streamable-http endpoint)"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<button
			type="submit"
			disabled={creating}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{creating ? 'Registering…' : 'Register server'}
		</button>
		{#if createError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if serverList.length === 0}
		<p class="text-sm text-neutral-500 italic">No MCP servers registered yet — add one above.</p>
	{:else}
		{#if rowError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{rowError}</p>
		{/if}
		<ul class="flex flex-col gap-2">
			{#each serverList as server (server.id)}
				<li class="rounded-lg border border-ink/12 px-4 py-3 dark:border-white/10">
					<div class="flex items-center justify-between">
						<div>
							<p class="font-medium text-ink dark:text-ink-dark">
								{server.name}
								<span
									class="ml-2 rounded-sm px-2 py-0.5 text-xs {server.connected
										? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
										: 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400'}"
								>
									{server.connected ? 'connected' : 'disconnected'}
								</span>
							</p>
							<p class="text-xs text-neutral-500">{server.url}</p>
							{#if server.connected}
								<p class="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
									{server.tools.length} tool{server.tools.length === 1 ? '' : 's'}:
									{server.tools.map((t) => t.name).join(', ')}
								</p>
							{/if}
						</div>
						<div class="flex shrink-0 items-center gap-3">
							<button
								onclick={() => handleReconnect(server.id)}
								disabled={reconnectingId === server.id}
								class="text-xs text-neutral-600 hover:text-agent-cyan-700 disabled:opacity-50 dark:text-neutral-400 dark:hover:text-agent-cyan-400"
							>
								{reconnectingId === server.id ? 'Reconnecting…' : 'Reconnect'}
							</button>
							<button
								onclick={() => handleDelete(server.id)}
								class="text-xs text-neutral-500 hover:text-agent-magenta-600"
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
