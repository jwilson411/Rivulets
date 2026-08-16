<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import {
		mcpServers,
		type MCPServerDetail,
		type MCPTool,
		type MCPTransport
	} from '$lib/api/mcpServers';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// MCP servers (06-screens.md → MCP servers, mockup 2j): a list with a
	// connected chip, and an "Add an MCP server" sheet asking how it
	// connects — "Web address" or "App on this machine", never transport
	// slugs (banned-words list). Headers/env live behind More options.

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
	// Shared by auth headers and subprocess env vars, which have identical shape.
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

	let serverList = $state<MCPServerDetail[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let adding = $state(false);
	let name = $state('');
	let transport = $state<MCPTransport>('streamable-http');
	let url = $state('');
	let headerLines = $state('');
	let command = $state('');
	let argLines = $state('');
	let envLines = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let openServer = $state<MCPServerDetail | null>(null);
	let sheetError = $state<string | null>(null);
	let reconnecting = $state(false);
	let editSecretLines = $state('');
	let savingSecrets = $state(false);
	let confirmingRemove = $state(false);

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';
	const argsPlaceholder = '-y\n@modelcontextprotocol/server-filesystem\n/path/to/dir';
	const monoAreaClass =
		'rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark';

	async function refresh() {
		loadError = null;
		try {
			// The list endpoint doesn't include tools, so fetch details for
			// each server to show its discovered tool count inline.
			const servers = await mcpServers.list();
			serverList = await Promise.all(servers.map((s) => mcpServers.get(s.id)));
			if (openServer) {
				openServer = serverList.find((s) => s.id === openServer!.id) ?? null;
			}
		} catch {
			loadError = "Couldn't load MCP servers.";
		} finally {
			loading = false;
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

		let body;
		if (transport === 'streamable-http') {
			if (!url.trim()) return;
			let headers: Record<string, string> | undefined;
			if (headerLines.trim()) {
				try {
					headers = parseKeyValueLines(headerLines);
				} catch (err) {
					createError = err instanceof Error ? err.message : 'Those headers don’t look right.';
					return;
				}
			}
			body = { name: name.trim(), transport, url: url.trim(), headers };
		} else {
			if (!command.trim()) return;
			const args = parseArgLines(argLines);
			let env: Record<string, string> | undefined;
			if (envLines.trim()) {
				try {
					env = parseKeyValueLines(envLines);
				} catch (err) {
					createError = err instanceof Error ? err.message : 'Those variables don’t look right.';
					return;
				}
			}
			body = {
				name: name.trim(),
				transport,
				command: command.trim(),
				args: args.length > 0 ? args : undefined,
				env
			};
		}

		createBusy = true;
		try {
			await mcpServers.create(body);
			resetForm();
			adding = false;
			await refresh();
		} catch (err) {
			createError = err instanceof ApiError ? err.message : "Couldn't connect that server.";
		} finally {
			createBusy = false;
		}
	}

	function openDetail(server: MCPServerDetail) {
		sheetError = null;
		editSecretLines = '';
		confirmingRemove = false;
		openServer = server;
	}

	async function handleReconnect() {
		if (!openServer) return;
		sheetError = null;
		reconnecting = true;
		try {
			await mcpServers.reconnect(openServer.id);
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't reconnect.";
		} finally {
			reconnecting = false;
		}
	}

	// Full replace on save — leaving the box blank and saving clears all.
	async function handleSaveSecrets() {
		if (!openServer) return;
		sheetError = null;
		let parsed: Record<string, string>;
		try {
			parsed = parseKeyValueLines(editSecretLines);
		} catch (err) {
			sheetError = err instanceof Error ? err.message : 'Those lines don’t look right.';
			return;
		}
		savingSecrets = true;
		try {
			if (openServer.transport === 'stdio') {
				await mcpServers.setEnv(openServer.id, parsed);
			} else {
				await mcpServers.setHeaders(openServer.id, parsed);
			}
			editSecretLines = '';
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't save. Owner access needed.";
		} finally {
			savingSecrets = false;
		}
	}

	async function handleRemove() {
		if (!openServer) return;
		sheetError = null;
		try {
			await mcpServers.remove(openServer.id);
			openServer = null;
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't remove this server.";
			confirmingRemove = false;
		}
	}
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">MCP servers</h1>
		<Button onclick={() => (adding = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			Add an MCP server
		</Button>
	</div>

	<p class="mb-6 max-w-[60ch] text-[15px] leading-normal text-muted dark:text-muted-dark">
		MCP connects outside tools to your agents. Add a server and its tools show up under Tools, next
		to the built-in ones. A server that fails to connect is still saved — open it to retry.
	</p>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if serverList.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			No MCP servers connected yet.
		</p>
	{:else}
		<div class="flex flex-col gap-2">
			{#each serverList as server (server.id)}
				<button
					type="button"
					onclick={() => openDetail(server)}
					class="flex min-h-14 w-full flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 text-left hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">
						{server.name}
					</span>
					{#if server.connected}
						<span class="text-[13px] text-muted dark:text-muted-dark">
							{server.tools.length} tool{server.tools.length === 1 ? '' : 's'} discovered
						</span>
					{/if}
					<StatusPill
						tone={server.connected ? 'accent' : 'neutral'}
						dot
						class="ml-auto h-[22px] text-xs"
					>
						{server.connected ? 'Connected' : 'Not connected'}
					</StatusPill>
				</button>
			{/each}
		</div>
	{/if}
</div>

{#if adding}
	<Sheet title="Add an MCP server" onClose={() => (adding = false)}>
		<form id="new-mcp-form" onsubmit={handleCreate} class="flex flex-col gap-5">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="mcp-name">Name</label>
				<input
					id="mcp-name"
					type="text"
					bind:value={name}
					placeholder="Filesystem tools"
					class={inputClass}
				/>
			</div>

			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">How it connects</span>
				<div class="flex gap-2">
					<button
						type="button"
						onclick={() => (transport = 'streamable-http')}
						aria-pressed={transport === 'streamable-http'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {transport ===
						'streamable-http'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						Web address
					</button>
					<button
						type="button"
						onclick={() => (transport = 'stdio')}
						aria-pressed={transport === 'stdio'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {transport ===
						'stdio'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						App on this machine
					</button>
				</div>
			</div>

			{#if transport === 'streamable-http'}
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="mcp-url">
						Address
					</label>
					<input
						id="mcp-url"
						type="text"
						bind:value={url}
						placeholder="http://localhost:9310/mcp"
						class="{inputClass} font-mono text-sm"
					/>
				</div>
			{:else}
				<p
					class="rounded-xl border border-warn-line bg-warn-soft px-4 py-3 text-[13px] leading-normal text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
				>
					An app server runs as a local program Rivulets starts and controls — a bigger security
					surface than a web call. Owner only.
				</p>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="mcp-command">
						Command
					</label>
					<input
						id="mcp-command"
						type="text"
						bind:value={command}
						placeholder="npx"
						class="{inputClass} font-mono text-sm"
					/>
				</div>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="mcp-args">
						Arguments — one per line
					</label>
					<textarea
						id="mcp-args"
						rows="3"
						bind:value={argLines}
						placeholder={argsPlaceholder}
						class={monoAreaClass}></textarea>
				</div>
			{/if}

			<details>
				<summary
					class="flex cursor-pointer items-center gap-2 text-base font-medium text-ink dark:text-ink-dark"
				>
					<Icon name="chevron-right" class="h-4 w-4 text-muted dark:text-muted-dark" />
					More options
					<span class="text-sm font-normal text-muted dark:text-muted-dark">
						{transport === 'streamable-http' ? 'Headers' : 'Environment'}
					</span>
				</summary>
				<div class="mt-3 flex flex-col gap-2">
					<p class="text-[13px] text-muted dark:text-muted-dark">
						One per line, as <code class="font-mono">Name: value</code>. Owner only — values are
						kept in your OS keychain and never shown again.
					</p>
					{#if transport === 'streamable-http'}
						<textarea
							rows="2"
							bind:value={headerLines}
							placeholder="Authorization: Bearer sk-…"
							aria-label="Auth headers"
							class={monoAreaClass}></textarea>
					{:else}
						<textarea
							rows="2"
							bind:value={envLines}
							placeholder="API_KEY: sk-…"
							aria-label="Environment variables"
							class={monoAreaClass}></textarea>
					{/if}
				</div>
			</details>

			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (adding = false)}>Cancel</Button>
			<Button
				disabled={createBusy ||
					!name.trim() ||
					(transport === 'streamable-http' ? !url.trim() : !command.trim())}
				onclick={() => (document.getElementById('new-mcp-form') as HTMLFormElement).requestSubmit()}
			>
				{createBusy ? 'Connecting…' : 'Connect'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if openServer && !confirmingRemove}
	<Sheet title={openServer.name} onClose={() => (openServer = null)}>
		<div class="flex flex-wrap items-center gap-3">
			<StatusPill tone={openServer.connected ? 'accent' : 'neutral'} dot>
				{openServer.connected ? 'Connected' : 'Not connected'}
			</StatusPill>
			<code class="min-w-0 truncate font-mono text-[13px] text-muted dark:text-muted-dark">
				{openServer.transport === 'stdio'
					? `${openServer.command} ${openServer.args.join(' ')}`
					: openServer.url}
			</code>
		</div>

		<Button
			variant="secondary"
			size="md"
			class="self-start"
			onclick={handleReconnect}
			disabled={reconnecting}
		>
			{reconnecting ? 'Reconnecting…' : 'Reconnect'}
		</Button>

		{#if openServer.connected && openServer.tools.length > 0}
			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">
					{openServer.tools.length} tool{openServer.tools.length === 1 ? '' : 's'} discovered
				</span>
				{#each openServer.tools as tool (tool.id)}
					<details class="rounded-lg border border-line px-3.5 py-2.5 dark:border-line-dark">
						<summary class="cursor-pointer">
							<span class="font-mono text-sm text-ink dark:text-ink-dark">{tool.name}</span>
							{#if hasConditionalArgs(tool)}
								<span
									class="ml-2 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent dark:bg-accent-soft-dark dark:text-accent-dark"
								>
									conditional inputs
								</span>
							{/if}
						</summary>
						<div class="mt-2 flex flex-col gap-2">
							{#if tool.description}
								<p class="text-[13px] text-muted dark:text-muted-dark">{tool.description}</p>
							{/if}
							<pre
								class="overflow-x-auto rounded-lg bg-paper p-3 font-mono text-[11px] text-ink dark:bg-paper-dark dark:text-ink-dark">{JSON.stringify(
									tool.input_schema,
									null,
									2
								)}</pre>
						</div>
					</details>
				{/each}
			</div>
		{/if}

		<div class="flex flex-col gap-2 border-t border-line pt-4 dark:border-line-dark">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">
				{openServer.transport === 'stdio' ? 'Environment' : 'Headers'}
			</span>
			{#if (openServer.transport === 'stdio' ? openServer.env_names : openServer.header_names).length > 0}
				<p class="text-[13px] text-muted dark:text-muted-dark">
					Currently set:
					{(openServer.transport === 'stdio' ? openServer.env_names : openServer.header_names).join(
						', '
					)} — values are never shown.
				</p>
			{/if}
			<p class="text-[13px] text-muted dark:text-muted-dark">
				One per line, as <code class="font-mono">Name: value</code>. Saving replaces the whole set;
				save blank to clear. Owner only.
			</p>
			<textarea
				rows="2"
				bind:value={editSecretLines}
				placeholder={openServer.transport === 'stdio'
					? 'API_KEY: sk-…'
					: 'Authorization: Bearer sk-…'}
				aria-label={openServer.transport === 'stdio' ? 'Environment variables' : 'Auth headers'}
				class={monoAreaClass}></textarea>
			<Button
				variant="secondary"
				size="md"
				class="self-start"
				onclick={handleSaveSecrets}
				disabled={savingSecrets}
			>
				{savingSecrets ? 'Saving…' : 'Save'}
			</Button>
		</div>

		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}

		{#snippet footer()}
			<button
				type="button"
				onclick={() => (confirmingRemove = true)}
				class="mr-auto text-[15px] font-medium text-danger hover:underline"
			>
				Remove server
			</button>
			<Button variant="secondary" onclick={() => (openServer = null)}>Close</Button>
		{/snippet}
	</Sheet>
{/if}

{#if openServer && confirmingRemove}
	<Sheet title="Remove {openServer.name}?" onClose={() => (confirmingRemove = false)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Its tools disappear from agents immediately.
		</p>
		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingRemove = false)}>Cancel</Button>
			<Button variant="destructive" onclick={handleRemove}>Remove server</Button>
		{/snippet}
	</Sheet>
{/if}
