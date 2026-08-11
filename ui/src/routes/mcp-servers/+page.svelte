<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import {
		mcpServers,
		type MCPServerDetail,
		type MCPTool,
		type MCPTransport
	} from '$lib/api/mcpServers';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';

	// JSON Schema keywords that make one argument's requiredness/shape depend
	// on another — surfaced as a badge so a user scanning the tool list can
	// spot conditional args without opening the raw schema.
	const CONDITIONAL_SCHEMA_KEYWORDS = ['if', 'dependentRequired', 'dependentSchemas', 'oneOf'];

	function hasConditionalArgs(tool: MCPTool): boolean {
		return CONDITIONAL_SCHEMA_KEYWORDS.some((keyword) => keyword in tool.input_schema);
	}

	// "Name: value" per line -- simpler to type/paste than a dynamic
	// row-add-remove widget for what's usually one or two entries, and
	// matches the shape a user copies straight out of an MCP server's docs.
	// Shared by auth headers and stdio env vars, which have identical shape.
	function parseKeyValueLines(text: string): Record<string, string> {
		const entries: Record<string, string> = {};
		for (const rawLine of text.split('\n')) {
			const line = rawLine.trim();
			if (!line) continue;
			const separator = line.indexOf(':');
			if (separator === -1) {
				throw new Error(`Line missing ":" — "${line}"`);
			}
			const key = line.slice(0, separator).trim();
			const value = line.slice(separator + 1).trim();
			if (!key) throw new Error(`Line missing a name — "${line}"`);
			entries[key] = value;
		}
		return entries;
	}

	// One arg per line -- avoids the ambiguity of splitting a single string
	// on spaces when an arg (e.g. a file path) legitimately contains one.
	function parseArgLines(text: string): string[] {
		return text
			.split('\n')
			.map((line) => line.trim())
			.filter((line) => line.length > 0);
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
	let transport = $state<MCPTransport>('streamable-http');
	let url = $state('');
	let headerLines = $state('');
	let command = $state('');
	let argLines = $state('');
	let envLines = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let rowError = $state<string | null>(null);
	let reconnectingId = $state<string | null>(null);

	let editingHeadersId = $state<string | null>(null);
	let editHeaderLines = $state('');
	let savingHeaders = $state(false);
	let headersError = $state<string | null>(null);

	let editingEnvId = $state<string | null>(null);
	let editEnvLines = $state('');
	let savingEnv = $state(false);
	let envError = $state<string | null>(null);

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

	function resetForm() {
		name = '';
		url = '';
		headerLines = '';
		command = '';
		argLines = '';
		envLines = '';
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (!name.trim()) return;

		if (transport === 'streamable-http') {
			if (!url.trim()) return;
			let headers: Record<string, string> | undefined;
			if (headerLines.trim()) {
				try {
					headers = parseKeyValueLines(headerLines);
				} catch (err) {
					createError = err instanceof Error ? err.message : 'Invalid headers';
					return;
				}
			}
			creating = true;
			try {
				await mcpServers.create({ name: name.trim(), transport, url: url.trim(), headers });
				resetForm();
				await refresh();
			} catch (err) {
				createError = err instanceof ApiError ? err.message : 'Failed to register MCP server';
			} finally {
				creating = false;
			}
			return;
		}

		if (!command.trim()) return;
		const args = parseArgLines(argLines);
		let env: Record<string, string> | undefined;
		if (envLines.trim()) {
			try {
				env = parseKeyValueLines(envLines);
			} catch (err) {
				createError = err instanceof Error ? err.message : 'Invalid env vars';
				return;
			}
		}
		creating = true;
		try {
			await mcpServers.create({
				name: name.trim(),
				transport,
				command: command.trim(),
				args: args.length > 0 ? args : undefined,
				env
			});
			resetForm();
			await refresh();
		} catch (err) {
			createError = err instanceof ApiError ? err.message : 'Failed to register MCP server';
		} finally {
			creating = false;
		}
	}

	function openHeaderEditor(id: string) {
		headersError = null;
		editHeaderLines = '';
		editingHeadersId = id;
	}

	async function handleSaveHeaders(id: string) {
		headersError = null;
		let headers: Record<string, string>;
		try {
			headers = parseKeyValueLines(editHeaderLines);
		} catch (err) {
			headersError = err instanceof Error ? err.message : 'Invalid headers';
			return;
		}
		savingHeaders = true;
		try {
			await mcpServers.setHeaders(id, headers);
			editingHeadersId = null;
			await refresh();
		} catch (err) {
			headersError = err instanceof ApiError ? err.message : 'Failed to save headers';
		} finally {
			savingHeaders = false;
		}
	}

	async function handleClearHeaders(id: string) {
		headersError = null;
		savingHeaders = true;
		try {
			await mcpServers.setHeaders(id, {});
			editingHeadersId = null;
			await refresh();
		} catch (err) {
			headersError = err instanceof ApiError ? err.message : 'Failed to clear headers';
		} finally {
			savingHeaders = false;
		}
	}

	function openEnvEditor(id: string) {
		envError = null;
		editEnvLines = '';
		editingEnvId = id;
	}

	async function handleSaveEnv(id: string) {
		envError = null;
		let env: Record<string, string>;
		try {
			env = parseKeyValueLines(editEnvLines);
		} catch (err) {
			envError = err instanceof Error ? err.message : 'Invalid env vars';
			return;
		}
		savingEnv = true;
		try {
			await mcpServers.setEnv(id, env);
			editingEnvId = null;
			await refresh();
		} catch (err) {
			envError = err instanceof ApiError ? err.message : 'Failed to save env vars';
		} finally {
			savingEnv = false;
		}
	}

	async function handleClearEnv(id: string) {
		envError = null;
		savingEnv = true;
		try {
			await mcpServers.setEnv(id, {});
			editingEnvId = null;
			await refresh();
		} catch (err) {
			envError = err instanceof ApiError ? err.message : 'Failed to clear env vars';
		} finally {
			savingEnv = false;
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
				Streamable-HTTP servers need an endpoint URL, e.g.
				<code class="font-mono">http://localhost:3001/mcp</code>. Stdio servers run as a local
				command Rivulets spawns itself, e.g. <code class="font-mono">npx -y some-mcp-server</code>.
				See the
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
		<fieldset class="flex items-center gap-4 text-sm text-ink dark:text-ink-dark">
			<legend class="sr-only">Transport</legend>
			<label class="flex items-center gap-1.5">
				<input type="radio" bind:group={transport} value="streamable-http" />
				Streamable-HTTP
			</label>
			<label class="flex items-center gap-1.5">
				<input type="radio" bind:group={transport} value="stdio" />
				Stdio (local command)
			</label>
		</fieldset>

		{#if transport === 'streamable-http'}
			<input
				type="text"
				bind:value={url}
				placeholder="URL (streamable-http endpoint)"
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<details class="group">
				<summary
					class="cursor-pointer text-xs text-neutral-600 marker:content-none dark:text-neutral-400"
				>
					Advanced: auth headers
				</summary>
				<div class="mt-2 flex flex-col gap-1">
					<p class="text-xs text-neutral-500">
						One per line, as <code>Header-Name: value</code> — e.g.
						<code>Authorization: Bearer sk-...</code>. Requires an owner session; values are stored
						in your OS keychain, never shown again once saved.
					</p>
					<textarea
						bind:value={headerLines}
						rows="2"
						placeholder="Authorization: Bearer sk-..."
						class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					></textarea>
				</div>
			</details>
		{:else}
			<p
				class="bg-agent-magenta-50 dark:bg-agent-magenta-950/20 rounded-md border border-agent-magenta-600/40 p-2 text-xs text-ink dark:text-ink-dark"
			>
				Stdio servers run as a local subprocess Rivulets spawns and controls — a bigger security
				surface than a network call. Requires an owner session.
			</p>
			<input
				type="text"
				bind:value={command}
				placeholder="Command (e.g. npx)"
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<textarea
				bind:value={argLines}
				rows="2"
				placeholder="-y
@modelcontextprotocol/server-filesystem
/path/to/dir"
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			></textarea>
			<p class="text-xs text-neutral-500">One argument per line.</p>
			<details class="group">
				<summary
					class="cursor-pointer text-xs text-neutral-600 marker:content-none dark:text-neutral-400"
				>
					Advanced: env vars
				</summary>
				<div class="mt-2 flex flex-col gap-1">
					<p class="text-xs text-neutral-500">
						One per line, as <code>NAME: value</code> — e.g. <code>API_KEY: sk-...</code>. Values
						are stored in your OS keychain, never shown again once saved.
					</p>
					<textarea
						bind:value={envLines}
						rows="2"
						placeholder="API_KEY: sk-..."
						class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					></textarea>
				</div>
			</details>
		{/if}

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
							{#if server.transport === 'stdio'}
								<p class="font-mono text-xs text-neutral-500">
									{server.command}
									{server.args.join(' ')}
								</p>
								{#if server.env_names.length > 0}
									<p class="mt-1 text-xs text-neutral-500">
										Env vars: {server.env_names.join(', ')}
									</p>
								{/if}
							{:else}
								<p class="text-xs text-neutral-500">{server.url}</p>
								{#if server.header_names.length > 0}
									<p class="mt-1 text-xs text-neutral-500">
										Auth headers: {server.header_names.join(', ')}
									</p>
								{/if}
							{/if}
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
							{#if server.transport === 'stdio'}
								<button
									onclick={() => openEnvEditor(server.id)}
									class="text-xs text-neutral-600 hover:text-agent-cyan-700 dark:text-neutral-400 dark:hover:text-agent-cyan-400"
								>
									{server.env_names.length > 0 ? 'Edit env vars' : 'Add env vars'}
								</button>
							{:else}
								<button
									onclick={() => openHeaderEditor(server.id)}
									class="text-xs text-neutral-600 hover:text-agent-cyan-700 dark:text-neutral-400 dark:hover:text-agent-cyan-400"
								>
									{server.header_names.length > 0 ? 'Edit headers' : 'Add headers'}
								</button>
							{/if}
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
					{#if editingHeadersId === server.id}
						<div class="mt-3 flex flex-col gap-2 border-t border-ink/10 pt-3 dark:border-white/10">
							<p class="text-xs text-neutral-500">
								One per line, as <code>Header-Name: value</code>. Replaces the full set — leave
								blank and save to clear all headers. Requires an owner session.
							</p>
							<textarea
								bind:value={editHeaderLines}
								rows="2"
								placeholder="Authorization: Bearer sk-..."
								class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							></textarea>
							{#if headersError}
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									{headersError}
								</p>
							{/if}
							<div class="flex items-center gap-3">
								<button
									onclick={() => handleSaveHeaders(server.id)}
									disabled={savingHeaders}
									class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
								>
									{savingHeaders ? 'Saving…' : 'Save headers'}
								</button>
								{#if server.header_names.length > 0}
									<button
										onclick={() => handleClearHeaders(server.id)}
										disabled={savingHeaders}
										class="text-xs text-neutral-500 hover:text-agent-magenta-600 disabled:opacity-50"
									>
										Clear all
									</button>
								{/if}
								<button
									onclick={() => (editingHeadersId = null)}
									class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									Cancel
								</button>
							</div>
						</div>
					{/if}
					{#if editingEnvId === server.id}
						<div class="mt-3 flex flex-col gap-2 border-t border-ink/10 pt-3 dark:border-white/10">
							<p class="text-xs text-neutral-500">
								One per line, as <code>NAME: value</code>. Replaces the full set — leave blank and
								save to clear all env vars. Requires an owner session.
							</p>
							<textarea
								bind:value={editEnvLines}
								rows="2"
								placeholder="API_KEY: sk-..."
								class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							></textarea>
							{#if envError}
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									{envError}
								</p>
							{/if}
							<div class="flex items-center gap-3">
								<button
									onclick={() => handleSaveEnv(server.id)}
									disabled={savingEnv}
									class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
								>
									{savingEnv ? 'Saving…' : 'Save env vars'}
								</button>
								{#if server.env_names.length > 0}
									<button
										onclick={() => handleClearEnv(server.id)}
										disabled={savingEnv}
										class="text-xs text-neutral-500 hover:text-agent-magenta-600 disabled:opacity-50"
									>
										Clear all
									</button>
								{/if}
								<button
									onclick={() => (editingEnvId = null)}
									class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									Cancel
								</button>
							</div>
						</div>
					{/if}
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
