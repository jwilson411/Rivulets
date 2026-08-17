<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { workflows, type FailedWorkflowRun, type Workflow } from '$lib/api/workflows';
	import { timeAgo } from '$lib/format';
	import { auth } from '$lib/api/auth.svelte';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Workflows (06-screens.md → Workflows list, mockup 2e): /name is the
	// title — the workflow name IS the slash command. Failed-run banner up
	// top with "Open conversation". New workflow opens a name sheet, then
	// the canvas.

	type Filter = 'all' | 'published' | 'draft';

	let workflowList = $state<Workflow[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let filter = $state<Filter>('all');

	// #94 (observability layer): failed runs across every workflow, not
	// just the one a human happens to be looking at -- see
	// api/workflows.py's list_failed_runs docstring for why this exists.
	let failedRuns = $state<FailedWorkflowRun[]>([]);

	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let deletingWorkflow = $state<Workflow | null>(null);
	let deleteBusy = $state(false);
	let deleteError = $state<string | null>(null);

	let visible = $derived(
		workflowList.filter((w) => {
			if (filter === 'published') return w.published;
			if (filter === 'draft') return !w.published;
			return true;
		})
	);

	async function refresh() {
		loadError = null;
		try {
			workflowList = await workflows.list();
			failedRuns = await workflows.listFailedRuns().catch(() => [] as FailedWorkflowRun[]);
		} catch {
			loadError = "Couldn't load workflows.";
		} finally {
			loading = false;
		}
	}

	refresh();

	function failureFor(workflow: Workflow): FailedWorkflowRun | null {
		return failedRuns.find((r) => r.workflow_id === workflow.id) ?? null;
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim()) return;
		createBusy = true;
		createError = null;
		try {
			const created = await workflows.create({
				name: newName.trim(),
				description: newDescription.trim() || null
			});
			creating = false;
			goto(resolve('/workflows/[id]', { id: created.id }));
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the workflow.";
		} finally {
			createBusy = false;
		}
	}

	async function handleDelete() {
		if (!deletingWorkflow) return;
		deleteBusy = true;
		deleteError = null;
		try {
			await workflows.remove(deletingWorkflow.id);
			deletingWorkflow = null;
			await refresh();
		} catch {
			deleteError = "Couldn't delete this workflow. Try again.";
		} finally {
			deleteBusy = false;
		}
	}
</script>

<div class="mx-auto max-w-[820px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-5 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Workflows</h1>
		<Button onclick={() => (creating = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New workflow
		</Button>
	</div>

	{#if failedRuns.length > 0}
		{@const run = failedRuns[0]}
		<div
			class="mb-5 flex flex-wrap items-center gap-3.5 rounded-xl border border-danger-line bg-danger-soft px-5 py-4 dark:border-danger-line-dark dark:bg-danger-soft-dark"
		>
			<span class="text-[15px] font-semibold text-danger">Failed runs</span>
			<span class="min-w-0 flex-1 truncate text-[15px] text-danger-ink dark:text-danger-ink-dark">
				/{run.workflow_name}{run.error_message ? ` — ${run.error_message}` : ''} · {timeAgo(
					run.started_at
				)}
			</span>
			<a
				href={resolve('/channels/[id]/rivulets/[rivuletId]', {
					id: run.channel_id,
					rivuletId: run.rivulet_id
				})}
				class="inline-flex h-10 flex-none items-center rounded-lg border border-danger bg-surface px-4 text-[15px] font-semibold text-danger dark:bg-surface-dark"
			>
				Open conversation
			</a>
		</div>
	{/if}

	<div class="mb-5 flex gap-2">
		<FilterChip selected={filter === 'all'} onclick={() => (filter = 'all')}>All</FilterChip>
		<FilterChip selected={filter === 'published'} onclick={() => (filter = 'published')}>
			Published
		</FilterChip>
		<FilterChip selected={filter === 'draft'} onclick={() => (filter = 'draft')}>Draft</FilterChip>
	</div>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if visible.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">No workflows yet.</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each visible as workflow (workflow.id)}
				{@const failure = failureFor(workflow)}
				<div
					class="flex min-h-16 items-center gap-3.5 rounded-2xl border border-line bg-surface px-6 py-5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<a
						href={resolve('/workflows/[id]', { id: workflow.id })}
						class="flex min-w-0 flex-1 flex-wrap items-center gap-3.5"
					>
						<span class="font-mono text-base font-medium text-ink dark:text-ink-dark">
							/{workflow.name}
						</span>
						<StatusPill tone={workflow.published ? 'accent' : 'neutral'}>
							{workflow.published ? 'Published' : 'Draft'}
						</StatusPill>
						<span class="ml-auto text-sm text-muted dark:text-muted-dark">
							{failure
								? `Last run failed · ${timeAgo(failure.started_at)}`
								: `Updated ${timeAgo(workflow.updated_at)}`}
						</span>
					</a>
					{#if auth.grant === 'owner'}
						<button
							type="button"
							onclick={() => (deletingWorkflow = workflow)}
							class="flex-none text-sm font-medium text-muted hover:text-danger dark:text-muted-dark"
						>
							Delete
						</button>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if creating}
	<Sheet title="New workflow" onClose={() => (creating = false)} width={480}>
		<form id="new-workflow-form" onsubmit={handleCreate} class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-workflow-name">
					Name
				</label>
				<input
					id="new-workflow-name"
					type="text"
					bind:value={newName}
					placeholder="retry-check"
					class="h-12 rounded-lg border border-line bg-surface px-4 font-mono text-sm text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
				/>
				<p class="text-[13px] text-muted dark:text-muted-dark">
					This is also the command: /{newName.trim() || 'retry-check'}
				</p>
			</div>
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-workflow-desc">
					What it does
				</label>
				<input
					id="new-workflow-desc"
					type="text"
					bind:value={newDescription}
					placeholder="Optional"
					class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
				/>
			</div>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy || !newName.trim()}
				onclick={() =>
					(document.getElementById('new-workflow-form') as HTMLFormElement).requestSubmit()}
			>
				Create workflow
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if deletingWorkflow}
	<Sheet
		title="Delete /{deletingWorkflow.name}?"
		onClose={() => (deletingWorkflow = null)}
		width={480}
	>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Its slash command stops working and its runs stop being listed. Conversations stay.
		</p>
		{#if deleteError}
			<p class="text-sm text-danger">{deleteError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (deletingWorkflow = null)}>Cancel</Button>
			<Button variant="destructive" onclick={handleDelete} disabled={deleteBusy}>
				{deleteBusy ? 'Deleting…' : 'Delete workflow'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
