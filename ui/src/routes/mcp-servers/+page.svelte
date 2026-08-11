<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { mcpServers, type MCPServerDetail, type MCPTool } from '$lib/api/mcpServers';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';

	// JSON Schema keywords that make one argument's requiredness/shape depend
	// on another — surfaced as a badge so a user scanning the tool list can
	// spot conditional args without opening the raw schema.
	const CONDITIONAL_SCHEMA_KEYWORDS = ['if', 'dependentRequired', 'dependentSchemas', 'oneOf'];

	function hasConditionalArgs(tool: MCPTool): boolean {
		return CONDITIONAL_SCHEMA_KEYWORDS.some((keyword) => keyword in tool.input_schema);
	}

	const serverFilters: ListFilter<MCPServerDetail>[] = [
		{
			id: 'connection',
			label: 'Status',
			options: [
				{ value: 'connected', label: 'Connected' },
				{ value: 'disconnected', label: 'Disconnected' }
			],
			predicate: (server, value) => (value === 'connected' ? server.connected : !server.connected)
		}
	];

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
	<header class="flex flex-col gap-3">
		<div>
			<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">MCP Servers</h1>
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				A server that fails to connect is still saved — reconnect to retry.
			</p>
		</div>
		<div
			class="bg-agent-cyan-50 dark:bg-agent-cyan-950/20 flex flex-col gap-1 rounded-md border border-agent-cyan-600/40 p-3 text-xs text-ink dark:text-ink-dark"
		>
			<p>
				<span class="font-medium">MCP (Model Context Protocol)</span> is an open standard for connecting
				external tools to AI agents. Register a server below and its tools become available for your agents
				to use, alongside Rivulets' built-in tools.
			</p>
			<p class="text-neutral-600 dark:text-neutral-400">
				You'll need the server's streamable-HTTP endpoint URL, e.g.
				<code class="font-mono">http://localhost:3001/mcp</code>. See the
				<a
					href="https://modelcontextprotocol.io"
					target="_blank"
					rel="noreferrer"
					class="underline hover:text-agent-cyan-700 dark:hover:text-agent-cyan-400"
				>
					MCP docs
				</a>
				for where to find or how to run one.
			</p>
		</div>
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
			placeholder="URL (e.g. http://localhost:3001/mcp)"
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
	{:else}
		{#if rowError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{rowError}</p>
		{/if}
		<FilterableList
			items={serverList}
			getKey={(server) => server.id}
			searchPlaceholder="Search MCP servers…"
			searchPredicate={(server, q) => server.name.toLowerCase().includes(q.toLowerCase())}
			filters={serverFilters}
			emptyMessage="No MCP servers registered yet — add one above."
			noMatchMessage="No MCP servers match your search or filter."
		>
			{#snippet item(server)}
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
									{server.tools.length} tool{server.tools.length === 1 ? '' : 's'}
								</p>
								<ul class="mt-2 flex flex-col gap-1">
									{#each server.tools as tool (tool.id)}
										<li>
											<details class="group">
												<summary
													class="cursor-pointer text-xs text-neutral-700 marker:content-none dark:text-neutral-300"
												>
													<span class="font-mono">{tool.name}</span>
													{#if hasConditionalArgs(tool)}
														<span
															class="ml-1 rounded-sm bg-agent-cyan-100 px-1.5 py-0.5 text-[10px] text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400"
														>
															conditional args
														</span>
													{/if}
												</summary>
												<div class="mt-1 ml-3 flex flex-col gap-1">
													{#if tool.description}
														<p class="text-xs text-neutral-500">{tool.description}</p>
													{/if}
													<pre
														class="overflow-x-auto rounded-md bg-ink/5 p-2 text-[11px] text-ink dark:bg-white/5 dark:text-ink-dark">{JSON.stringify(
															tool.input_schema,
															null,
															2
														)}</pre>
												</div>
											</details>
										</li>
									{/each}
								</ul>
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
			{/snippet}
		</FilterableList>
	{/if}
</div>
