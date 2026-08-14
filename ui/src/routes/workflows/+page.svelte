<script lang="ts">
	import { resolve } from '$app/paths';
	import { workflows, type FailedWorkflowRun, type Workflow } from '$lib/api/workflows';
	import { timeAgo } from '$lib/format';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';
	import { auth } from '$lib/api/auth.svelte';

	const workflowFilters: ListFilter<Workflow>[] = [
		{
			id: 'status',
			label: 'Status',
			options: [
				{ value: 'published', label: 'Published' },
				{ value: 'draft', label: 'Draft' }
			],
			predicate: (workflow, value) =>
				value === 'published' ? workflow.published : !workflow.published
		}
	];

	let workflowList = $state<Workflow[]>([]);
	let loadError = $state<string | null>(null);

	// #94 (observability layer): failed runs across every workflow, not
	// just the one a human happens to be looking at -- see
	// api/workflows.py's list_failed_runs docstring for why this exists.
	let failedRuns = $state<FailedWorkflowRun[]>([]);
	let failedRunsError = $state<string | null>(null);

	async function refreshFailedRuns() {
		failedRunsError = null;
		try {
			failedRuns = await workflows.listFailedRuns();
		} catch (err) {
			failedRunsError = err instanceof Error ? err.message : 'Failed to load failed runs';
		}
	}

	refreshFailedRuns();

	let newName = $state('');
	let newDescription = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);

	let actionError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			workflowList = await workflows.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load workflows';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim()) return;
		createError = null;
		creating = true;
		try {
			await workflows.create({
				name: newName.trim(),
				description: newDescription.trim() || null
			});
			newName = '';
			newDescription = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create workflow';
		} finally {
			creating = false;
		}
	}

	const NAME_PATTERN = '[a-z][a-z0-9-]{1,63}';

	async function handleDelete(workflowId: string) {
		actionError = null;
		try {
			await workflows.remove(workflowId);
			await refresh();
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to delete workflow';
		}
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Workflows</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Saved, reusable chains of agents and utility steps (#24). A new workflow starts as a draft —
			publish it from the builder to trigger it from any channel with
			<code class="font-mono">/&#123;name&#125; &lt;input&gt;</code>.
		</p>
	</header>

	{#if failedRunsError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{failedRunsError}</p>
	{:else if failedRuns.length > 0}
		<section
			class="bg-agent-magenta-50 dark:bg-agent-magenta-950/20 flex flex-col gap-3 rounded-lg border border-agent-magenta-300 p-4 dark:border-agent-magenta-900/50"
		>
			<h2 class="text-sm font-medium text-agent-magenta-800 dark:text-agent-magenta-400">
				Failed runs ({failedRuns.length})
			</h2>
			<ul class="flex flex-col gap-2">
				{#each failedRuns as run (run.id)}
					<li class="flex items-start justify-between gap-3 text-sm">
						<div class="min-w-0 flex-1">
							<a
								href={resolve('/workflows/[id]', { id: run.workflow_id })}
								class="font-medium text-ink hover:underline dark:text-ink-dark"
							>
								/{run.workflow_name}
							</a>
							{#if run.error_message}
								<p class="truncate text-neutral-600 dark:text-neutral-400">
									{run.error_message}
								</p>
							{/if}
						</div>
						<div class="flex flex-none items-center gap-3 text-xs text-neutral-500">
							<span>{timeAgo(run.started_at)}</span>
							<a
								href={resolve('/channels/[id]/rivulets/[rivuletId]', {
									id: run.channel_id,
									rivuletId: run.rivulet_id
								})}
								class="text-agent-cyan-700 hover:underline dark:text-agent-cyan-400"
							>
								View rivulet
							</a>
						</div>
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">New workflow</h2>
		<input
			type="text"
			bind:value={newName}
			placeholder="my-workflow"
			pattern={NAME_PATTERN}
			title="lowercase letters, numbers, and hyphens, starting with a letter"
			class="rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		<p class="text-xs text-neutral-500">
			This becomes a slash command in your channels, e.g. <code class="font-mono"
				>/{newName.trim() || 'my-workflow'}</code
			> — lowercase letters, numbers, and hyphens only.
		</p>
		<input
			type="text"
			bind:value={newDescription}
			placeholder="Description (optional)"
			class="rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		{#if createError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}
		<button
			type="submit"
			disabled={creating || !newName.trim()}
			class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-sm font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{creating ? 'Creating…' : 'Create workflow'}
		</button>
	</form>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else}
		{#if actionError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{actionError}</p>
		{/if}
		<FilterableList
			items={workflowList}
			getKey={(workflow) => workflow.id}
			searchPlaceholder="Search workflows…"
			searchPredicate={(workflow, q) => workflow.name.toLowerCase().includes(q.toLowerCase())}
			filters={workflowFilters}
			emptyMessage="No workflows yet — create one above to start chaining agents and steps together."
			noMatchMessage="No workflows match your search or filter."
		>
			{#snippet item(workflow)}
				<li
					class="flex items-start justify-between rounded-lg border border-ink/12 p-4 dark:border-white/10"
				>
					<a href={resolve('/workflows/[id]', { id: workflow.id })} class="min-w-0 flex-1">
						<p class="flex items-center gap-2 font-medium text-ink dark:text-ink-dark">
							<span class="text-neutral-500">/</span>{workflow.name}
							<span
								class="rounded-sm px-1.5 py-0.5 text-[11px] font-normal {workflow.published
									? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
									: 'bg-neutral-200 text-neutral-700 dark:bg-white/10 dark:text-neutral-300'}"
							>
								{workflow.published ? 'Published' : 'Draft'}
							</span>
						</p>
						{#if workflow.description}
							<p class="text-sm text-neutral-600 dark:text-neutral-400">
								{workflow.description}
							</p>
						{/if}
						<p class="mt-1 text-xs text-neutral-500">Updated {timeAgo(workflow.updated_at)}</p>
					</a>
					{#if auth.grant === 'owner'}
						<button
							onclick={() => handleDelete(workflow.id)}
							class="ml-3 flex-none text-xs text-neutral-500 hover:text-agent-magenta-600"
						>
							Delete
						</button>
					{/if}
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
