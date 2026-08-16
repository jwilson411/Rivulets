<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/api/auth.svelte';
	import { tools, type Tool, type ToolVersion } from '$lib/api/tools';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SectionLabel from '$lib/ui/SectionLabel.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Tools (06-screens.md → Tools, mockup 2i): grouped Built in / Yours /
	// From MCP. Creating a custom tool leads with "describe what it should
	// do"; pasting code lives behind More. Custom tools' source, versions,
	// and delete live in a sheet (owner-only server-side, #321/#351).

	let toolList = $state<Tool[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let creating = $state(false);
	let createMode = $state<'simple' | 'advanced'>('simple');
	let name = $state('');
	let description = $state('');
	let prompt = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let openTool = $state<Tool | null>(null);
	let openVersions = $state<ToolVersion[]>([]);
	let sourceDraft = $state('');
	let editorPath = $state<string | null>(null);
	let sheetError = $state<string | null>(null);
	let confirmingDelete = $state(false);

	const groups: { label: string; type: Tool['tool_type'] }[] = [
		{ label: 'Built in', type: 'builtin' },
		{ label: 'Yours', type: 'custom' },
		{ label: 'From MCP', type: 'mcp' }
	];

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';

	async function refresh() {
		loadError = null;
		try {
			toolList = await tools.list();
		} catch {
			loadError = "Couldn't load tools.";
		} finally {
			loading = false;
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (!name.trim() || !description.trim()) return;
		if (createMode === 'simple' && !prompt.trim()) return;
		createBusy = true;
		try {
			await tools.create({
				name: name.trim(),
				description: description.trim(),
				mode: createMode,
				...(createMode === 'simple' ? { prompt: prompt.trim() } : {})
			});
			creating = false;
			name = '';
			description = '';
			prompt = '';
			await refresh();
		} catch (err) {
			// #133: Simple mode's codegen isn't wired up on every server —
			// offer a concrete next step instead of leaving a non-coder stuck.
			if (err instanceof ApiError && err.status === 501) {
				createError =
					"Describing a tool isn't available on this machine yet. Your name and description are kept — switch to pasting code, or ask an agent in a channel to write it for you.";
			} else {
				createError = err instanceof ApiError ? err.message : "Couldn't create the tool.";
			}
		} finally {
			createBusy = false;
		}
	}

	// #321: versions carry source_code and are OwnerGrant-only server-side
	// -- an invite-grant session never opens this sheet.
	async function openCustomTool(tool: Tool) {
		sheetError = null;
		editorPath = null;
		confirmingDelete = false;
		try {
			openVersions = await tools.listVersions(tool.id);
			sourceDraft = openVersions[0]?.source_code ?? '';
			openTool = tool;
		} catch {
			loadError = "Couldn't open that tool. Try again.";
		}
	}

	async function handleOpenEditor() {
		if (!openTool) return;
		sheetError = null;
		try {
			const result = await tools.openEditor(openTool.id);
			editorPath = result.path;
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't open the editor.";
		}
	}

	async function handleSaveVersion() {
		if (!openTool) return;
		sheetError = null;
		try {
			await tools.saveVersion(openTool.id, sourceDraft);
			openVersions = await tools.listVersions(openTool.id);
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't save that version.";
		}
	}

	async function handleRollback(version: ToolVersion) {
		if (!openTool) return;
		sheetError = null;
		try {
			await tools.rollback(openTool.id, version.version);
			sourceDraft = version.source_code;
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't roll back.";
		}
	}

	async function handleDelete() {
		if (!openTool) return;
		sheetError = null;
		try {
			await tools.remove(openTool.id);
			openTool = null;
			await refresh();
		} catch (err) {
			sheetError = err instanceof ApiError ? err.message : "Couldn't delete this tool.";
			confirmingDelete = false;
		}
	}
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Tools</h1>
		{#if auth.grant === 'owner'}
			<!-- #351: POST /tools is OwnerGrant-only server-side -- showing the
			     button to an invite-grant session just hands it a guaranteed
			     "Owner access required" error. -->
			<Button onclick={() => (creating = true)}>
				<Icon name="plus" class="h-[18px] w-[18px]" />
				New tool
			</Button>
		{/if}
	</div>

	{#if loading}
		<SkeletonCards count={3} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else}
		{#each groups as group (group.type)}
			{@const inGroup = toolList.filter((t) => t.tool_type === group.type)}
			{#if inGroup.length > 0}
				<SectionLabel class="mb-2.5 {group.type === 'builtin' ? '' : 'mt-6'}">
					{group.label}
				</SectionLabel>
				<div class="flex flex-col gap-2">
					{#each inGroup as tool (tool.id)}
						{@const clickable = tool.tool_type === 'custom' && auth.grant === 'owner'}
						<svelte:element
							this={clickable ? 'button' : 'div'}
							{...clickable ? { type: 'button', onclick: () => openCustomTool(tool) } : {}}
							class="flex min-h-14 w-full flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4.5 py-2 text-left dark:border-line-dark dark:bg-surface-dark {clickable
								? 'hover:border-accent dark:hover:border-accent-dark'
								: ''}"
						>
							<span class="font-mono text-sm font-medium text-ink dark:text-ink-dark">
								{tool.name}
							</span>
							{#if tool.sensitive}
								<StatusPill tone="warn" class="h-[22px] text-xs">Sensitive</StatusPill>
							{/if}
							<span class="hidden truncate text-[13px] text-muted sm:inline dark:text-muted-dark">
								{tool.description}
							</span>
							<StatusPill
								tone={tool.available ? 'accent' : 'neutral'}
								class="ml-auto h-[22px] text-xs"
							>
								{tool.available ? 'Available' : 'Unavailable on this machine'}
							</StatusPill>
						</svelte:element>
					{/each}
				</div>
			{/if}
		{/each}
		{#if toolList.length === 0}
			<p class="py-8 text-center text-base text-muted dark:text-muted-dark">No tools yet.</p>
		{/if}
	{/if}
</div>

{#if creating}
	<Sheet title="New tool" onClose={() => (creating = false)}>
		<form id="new-tool-form" onsubmit={handleCreate} class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-tool-name">
					Name
				</label>
				<input
					id="new-tool-name"
					type="text"
					bind:value={name}
					placeholder="fetch_release_notes"
					class="{inputClass} font-mono text-sm"
				/>
			</div>
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-tool-desc">
					What agents see
				</label>
				<input
					id="new-tool-desc"
					type="text"
					bind:value={description}
					placeholder="A short summary agents use to decide when to call it"
					class={inputClass}
				/>
			</div>
			{#if createMode === 'simple'}
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-tool-prompt">
						Describe what it should do
					</label>
					<textarea
						id="new-tool-prompt"
						rows="3"
						bind:value={prompt}
						placeholder="A model writes the code for you to review before it's registered."
						class="rounded-lg border border-line bg-surface px-4 py-3 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark"
					></textarea>
				</div>
				<button
					type="button"
					onclick={() => (createMode = 'advanced')}
					class="self-start text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
				>
					Paste code instead
				</button>
			{:else}
				<p class="text-[13px] text-muted dark:text-muted-dark">
					Creates an empty tool you write yourself — open it after creating to add the code.
				</p>
				<button
					type="button"
					onclick={() => (createMode = 'simple')}
					class="self-start text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
				>
					Describe it instead
				</button>
			{/if}
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy ||
					!name.trim() ||
					!description.trim() ||
					(createMode === 'simple' && !prompt.trim())}
				onclick={() =>
					(document.getElementById('new-tool-form') as HTMLFormElement).requestSubmit()}
			>
				{createBusy ? 'Creating…' : 'Create tool'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if openTool && !confirmingDelete}
	<Sheet title={openTool.name} onClose={() => (openTool = null)}>
		<p class="text-[15px] text-muted dark:text-muted-dark">{openTool.description}</p>

		<div class="flex flex-wrap items-center gap-3">
			<Button variant="secondary" size="md" onclick={handleOpenEditor}>Open in editor</Button>
			{#if editorPath}
				<code
					class="rounded-md bg-paper px-2.5 py-1.5 font-mono text-xs text-ink dark:bg-paper-dark dark:text-ink-dark"
				>
					{editorPath}
				</code>
			{/if}
		</div>
		<p class="text-[13px] text-muted dark:text-muted-dark">
			Edit the file in your editor, then paste the result below and save to register the new
			version.
		</p>

		<textarea
			rows="8"
			bind:value={sourceDraft}
			aria-label="Tool source code"
			class="rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
		></textarea>
		<Button size="md" class="self-start" onclick={handleSaveVersion}>Save version</Button>

		{#if openVersions.length > 0}
			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">History</span>
				{#each openVersions as version (version.version)}
					<div
						class="flex items-center justify-between gap-2 text-sm text-muted dark:text-muted-dark"
					>
						<span>v{version.version} · {new Date(version.created_at).toLocaleString()}</span>
						<button
							type="button"
							onclick={() => handleRollback(version)}
							class="font-semibold text-accent hover:underline dark:text-accent-dark"
						>
							Roll back
						</button>
					</div>
				{/each}
			</div>
		{/if}

		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}

		{#snippet footer()}
			<button
				type="button"
				onclick={() => (confirmingDelete = true)}
				class="mr-auto text-[15px] font-medium text-danger hover:underline"
			>
				Delete tool
			</button>
			<Button variant="secondary" onclick={() => (openTool = null)}>Close</Button>
		{/snippet}
	</Sheet>
{/if}

{#if openTool && confirmingDelete}
	<Sheet title="Delete {openTool.name}?" onClose={() => (confirmingDelete = false)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Agents using this tool lose it immediately.
		</p>
		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingDelete = false)}>Cancel</Button>
			<Button variant="destructive" onclick={handleDelete}>Delete tool</Button>
		{/snippet}
	</Sheet>
{/if}
