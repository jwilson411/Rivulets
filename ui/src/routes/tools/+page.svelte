<script lang="ts">
	import { resolve } from '$app/paths';
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/api/auth.svelte';
	import { tools, type Tool, type ToolVersion } from '$lib/api/tools';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';

	const toolFilters: ListFilter<Tool>[] = [
		{
			id: 'type',
			label: 'Type',
			options: [
				{ value: 'builtin', label: 'Built-in' },
				{ value: 'mcp', label: 'MCP' },
				{ value: 'custom', label: 'Custom' }
			],
			predicate: (tool, value) => tool.tool_type === value
		}
	];

	let toolList = $state<Tool[]>([]);
	let versionsByTool = $state<Record<string, ToolVersion[]>>({});
	let draftByTool = $state<Record<string, string>>({});
	let loadError = $state<string | null>(null);
	let rowError = $state<Record<string, string | null>>({});
	let editorPathByTool = $state<Record<string, string | null>>({});

	let createMode = $state<'simple' | 'advanced'>('advanced');
	let name = $state('');
	let description = $state('');
	let prompt = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	// #133: Simple mode's codegen isn't wired up on every server. When it
	// isn't, offer a concrete next step (switch modes, keep the fields the
	// user already filled in) instead of leaving a non-coder stuck.
	let simpleModeUnavailable = $state(false);

	function selectMode(mode: 'simple' | 'advanced') {
		createMode = mode;
		createError = null;
		simpleModeUnavailable = false;
	}

	async function refresh() {
		loadError = null;
		try {
			toolList = await tools.list();
			// #321: GET /tools/{id}/versions carries source_code and is now
			// OwnerGrant-only server-side -- an invite-grant session would
			// just get a 403 for each, so skip the fetch (and the panel
			// that would show it, below) entirely for non-owners.
			if (auth.grant !== 'owner') return;
			const customTools = toolList.filter((t) => t.tool_type === 'custom');
			const entries = await Promise.all(
				customTools.map(async (t) => [t.id, await tools.listVersions(t.id)] as const)
			);
			versionsByTool = Object.fromEntries(entries);
			// Don't clobber a version the user is currently mid-edit on — only
			// seed the draft the first time a tool's versions are seen.
			for (const [id, versions] of entries) {
				if (!(id in draftByTool) && versions.length > 0) {
					draftByTool[id] = versions[0].source_code;
				}
			}
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load tools';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		simpleModeUnavailable = false;
		if (!name.trim() || !description.trim()) return;
		if (createMode === 'simple' && !prompt.trim()) return;
		creating = true;
		try {
			await tools.create({
				name: name.trim(),
				description: description.trim(),
				mode: createMode,
				...(createMode === 'simple' ? { prompt: prompt.trim() } : {})
			});
			name = '';
			description = '';
			prompt = '';
			await refresh();
		} catch (err) {
			if (err instanceof ApiError && err.status === 501) {
				simpleModeUnavailable = true;
				createError =
					'Simple mode isn’t available on this server yet. Your name and description are kept below — switch to Advanced mode to write the code yourself, or ask an agent in a channel chat to write it for you and paste the result in once the tool is created.';
			} else {
				createError = err instanceof ApiError ? err.message : 'Failed to create tool';
			}
		} finally {
			creating = false;
		}
	}

	async function handleOpenEditor(id: string) {
		rowError[id] = null;
		try {
			const result = await tools.openEditor(id);
			editorPathByTool[id] = result.path;
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to open editor';
		}
	}

	async function handleSaveVersion(id: string) {
		rowError[id] = null;
		try {
			await tools.saveVersion(id, draftByTool[id] ?? '');
			versionsByTool[id] = await tools.listVersions(id);
			toolList = await tools.list();
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to save version';
		}
	}

	async function handleRollback(id: string, version: number, sourceCode: string) {
		rowError[id] = null;
		try {
			await tools.rollback(id, version);
			draftByTool[id] = sourceCode;
			toolList = await tools.list();
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to roll back';
		}
	}

	async function handleDelete(id: string) {
		rowError[id] = null;
		try {
			await tools.remove(id);
			delete versionsByTool[id];
			delete draftByTool[id];
			await refresh();
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to delete tool';
		}
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Tools</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Built-in, MCP-discovered, and custom tools available to agents. Custom tools are versioned and
			synced to every peer (FR-9.1, FR-8.2–FR-8.4).
		</p>
	</header>

	{#if auth.grant === 'owner'}
		<!-- #351: POST /tools is OwnerGrant-only server-side (like DELETE and the
	     version endpoints, #321) -- showing the form to an invite-grant
	     session just hands it a guaranteed "Owner access required" error. -->
		<form
			onsubmit={handleCreate}
			class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
		>
			<h2 class="text-sm font-medium text-ink dark:text-ink-dark">New custom tool</h2>
			<div
				role="group"
				aria-label="Creation mode"
				class="flex w-fit rounded-md border border-ink/15 p-0.5 dark:border-white/15"
			>
				<button
					type="button"
					aria-pressed={createMode === 'simple'}
					onclick={() => selectMode('simple')}
					class="rounded-[3px] px-3 py-1 text-xs {createMode === 'simple'
						? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
						: 'text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-white/5'}"
				>
					Simple mode
				</button>
				<button
					type="button"
					aria-pressed={createMode === 'advanced'}
					onclick={() => selectMode('advanced')}
					class="rounded-[3px] px-3 py-1 text-xs {createMode === 'advanced'
						? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
						: 'text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-white/5'}"
				>
					Advanced mode
				</button>
			</div>

			<input
				type="text"
				bind:value={name}
				placeholder="Name"
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<input
				type="text"
				bind:value={description}
				placeholder="Description"
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>

			{#if createMode === 'simple'}
				<textarea
					bind:value={prompt}
					placeholder="Describe what the tool should do — an LLM will generate the Agno SDK code for you to review before it's registered."
					rows="3"
					class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				></textarea>
				<p class="text-xs text-neutral-500">
					Code generation isn't available on every server. If it isn't here, you'll be able to
					switch to Advanced mode without losing your name and description, then write the code
					yourself or ask an agent in a channel chat to write it for you.
				</p>
			{:else}
				<p class="text-xs text-neutral-500">
					Creates an empty tool file you write by hand — use "Open in editor" below once it's
					created.
				</p>
			{/if}

			<button
				type="submit"
				disabled={creating}
				class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
			>
				{creating ? 'Creating…' : 'Create tool'}
			</button>
			{#if createError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
				{#if simpleModeUnavailable}
					<div class="flex flex-wrap gap-2">
						<button
							type="button"
							onclick={() => selectMode('advanced')}
							class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
						>
							Switch to Advanced mode
						</button>
						<a
							href={resolve('/channels')}
							class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
						>
							Ask an agent in a channel
						</a>
					</div>
				{/if}
			{/if}
		</form>
	{/if}

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else}
		<FilterableList
			items={toolList}
			getKey={(tool) => tool.id}
			searchPlaceholder="Search tools…"
			searchPredicate={(tool, q) => tool.name.toLowerCase().includes(q.toLowerCase())}
			filters={toolFilters}
			emptyMessage={auth.grant === 'owner' ? 'No tools yet — add one above.' : 'No tools yet.'}
			noMatchMessage="No tools match your search or filter."
		>
			{#snippet item(tool)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					<div class="flex items-start justify-between">
						<div>
							<p class="font-medium text-ink dark:text-ink-dark">
								{tool.name}
								<span
									class="ml-2 rounded-sm bg-neutral-200/60 px-2 py-0.5 text-xs text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
								>
									{tool.tool_type}
								</span>
								{#if !tool.available}
									<span
										class="ml-1 rounded-sm bg-agent-magenta-100 px-2 py-0.5 text-xs text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400"
									>
										unavailable
									</span>
								{/if}
								{#if tool.sensitive}
									<span
										class="ml-1 rounded-sm bg-agent-yellow-500/15 px-2 py-0.5 text-xs text-agent-yellow-700 dark:text-agent-yellow-500"
										title="Blocked from unattended use (schedules, remediation) unless the agent using it is approved."
									>
										sensitive
									</span>
								{/if}
							</p>
							<p class="text-sm text-neutral-600 dark:text-neutral-400">{tool.description}</p>
						</div>
						{#if tool.tool_type === 'custom' && auth.grant === 'owner'}
							<!-- DELETE /tools is OwnerGrant-only server-side (#351),
							     same as the version/source panel below. -->
							<button
								onclick={() => handleDelete(tool.id)}
								class="shrink-0 text-xs text-neutral-500 hover:text-agent-magenta-600"
							>
								Delete
							</button>
						{/if}
					</div>

					{#if tool.tool_type === 'custom' && auth.grant === 'owner'}
						<!-- #321: source_code (in the draft textarea and version history below)
						     is exactly what save_tool_version/list_tool_versions are OwnerGrant-gated
						     for server-side -- an invite-grant session can't fetch it, so there's
						     nothing here for it to see or act on. -->
						<div class="mt-3 flex flex-col gap-3 border-t border-ink/10 pt-3 dark:border-white/10">
							{#if rowError[tool.id]}
								<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
									{rowError[tool.id]}
								</p>
							{/if}

							<div class="flex flex-wrap items-center gap-2">
								<button
									onclick={() => handleOpenEditor(tool.id)}
									class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
								>
									Open in editor
								</button>
								{#if editorPathByTool[tool.id]}
									<code
										class="rounded-sm bg-neutral-200/60 px-2 py-1 text-xs text-ink dark:bg-white/10 dark:text-ink-dark"
									>
										{editorPathByTool[tool.id]}
									</code>
								{/if}
							</div>
							<p class="text-xs text-neutral-500">
								Copy this path into your editor of choice, edit the file, then paste the result
								below and save to register the new version (FR-8.4).
							</p>

							<textarea
								bind:value={draftByTool[tool.id]}
								rows="6"
								placeholder="Tool source code"
								class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							></textarea>
							<button
								onclick={() => handleSaveVersion(tool.id)}
								class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-agent-cyan-600"
							>
								Save version
							</button>

							{#if versionsByTool[tool.id]?.length}
								<div class="flex flex-col gap-1">
									<p class="text-xs font-medium text-ink dark:text-ink-dark">Version history</p>
									<ul class="flex flex-col gap-1">
										{#each versionsByTool[tool.id] as version (version.version)}
											<li
												class="flex items-center justify-between text-xs text-neutral-600 dark:text-neutral-400"
											>
												<span
													>v{version.version} — {new Date(
														version.created_at
													).toLocaleString()}</span
												>
												<button
													onclick={() =>
														handleRollback(tool.id, version.version, version.source_code)}
													class="text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
												>
													Roll back
												</button>
											</li>
										{/each}
									</ul>
								</div>
							{/if}
						</div>
					{/if}
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
