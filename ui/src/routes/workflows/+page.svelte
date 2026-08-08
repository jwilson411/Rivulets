<script lang="ts">
	import { resolve } from '$app/paths';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import { timeAgo } from '$lib/format';

	let workflowList = $state<Workflow[]>([]);
	let loadError = $state<string | null>(null);

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
			Saved, reusable chains of agents and utility steps (#24). Trigger a saved workflow from any
			channel with <code class="font-mono">/&#123;name&#125; &lt;input&gt;</code>.
		</p>
	</header>

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
		{#if workflowList.length === 0}
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				No workflows yet — create one above to start chaining agents and steps together.
			</p>
		{:else}
			<ul class="flex flex-col gap-3">
				{#each workflowList as workflow (workflow.id)}
					<li
						class="flex items-start justify-between rounded-lg border border-ink/12 p-4 dark:border-white/10"
					>
						<a href={resolve('/workflows/[id]', { id: workflow.id })} class="min-w-0 flex-1">
							<p class="font-medium text-ink dark:text-ink-dark">
								<span class="text-neutral-500">/</span>{workflow.name}
							</p>
							{#if workflow.description}
								<p class="text-sm text-neutral-600 dark:text-neutral-400">
									{workflow.description}
								</p>
							{/if}
							<p class="mt-1 text-xs text-neutral-500">Updated {timeAgo(workflow.updated_at)}</p>
						</a>
						<button
							onclick={() => handleDelete(workflow.id)}
							class="ml-3 flex-none text-xs text-neutral-500 hover:text-agent-magenta-600"
						>
							Delete
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>
