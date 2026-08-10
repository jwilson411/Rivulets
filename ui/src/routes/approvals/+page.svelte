<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { approvals, type PendingApproval } from '$lib/api/approvals';
	import { auth } from '$lib/api/auth.svelte';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';

	const approvalFilters: ListFilter<PendingApproval>[] = [
		{
			id: 'status',
			label: 'Status',
			options: [
				{ value: 'pending', label: 'Pending' },
				{ value: 'approved', label: 'Approved' },
				{ value: 'rejected', label: 'Rejected' }
			],
			predicate: (approval, value) => approval.status === value
		},
		{
			id: 'source_type',
			label: 'Source',
			options: [
				{ value: 'schedule', label: 'Schedule' },
				{ value: 'budget', label: 'Budget' },
				{ value: 'tool_guardrail', label: 'Unattended tool use' }
			],
			predicate: (approval, value) => approval.source_type === value
		}
	];

	const sourceLabels: Record<PendingApproval['source_type'], string> = {
		schedule: 'Schedule',
		budget: 'Budget',
		tool_guardrail: 'Unattended tool use'
	};

	let approvalList = $state<PendingApproval[]>([]);
	let loadError = $state<string | null>(null);
	let rowError = $state<Record<string, string | null>>({});
	let actingId = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			approvalList = await approvals.list();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load approvals';
		}
	}

	refresh();

	async function handleApprove(id: string) {
		rowError[id] = null;
		actingId = id;
		try {
			await approvals.approve(id);
			await refresh();
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to approve';
		} finally {
			actingId = null;
		}
	}

	async function handleReject(id: string) {
		rowError[id] = null;
		actingId = id;
		try {
			await approvals.reject(id);
			await refresh();
		} catch (err) {
			rowError[id] = err instanceof ApiError ? err.message : 'Failed to reject';
		} finally {
			actingId = null;
		}
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Approvals</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			One inbox for anything that needs a human's OK before it happens (#102) — an agent-created
			schedule (#93), a budget cap that's been exceeded (#97), or an agent blocked from using a
			sensitive tool unattended (#100).
		</p>
	</header>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else}
		<FilterableList
			items={approvalList}
			getKey={(approval) => approval.id}
			filters={approvalFilters}
			emptyMessage="Nothing waiting on approval."
			noMatchMessage="No approvals match the selected filters."
		>
			{#snippet item(approval)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<p class="font-medium text-ink dark:text-ink-dark">
								{approval.title}
								<span
									class="ml-2 rounded-sm bg-neutral-200/60 px-2 py-0.5 text-xs text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
								>
									{sourceLabels[approval.source_type]}
								</span>
							</p>
							<p class="mt-1 text-sm text-neutral-600 dark:text-neutral-400">{approval.detail}</p>
						</div>
						{#if approval.status === 'pending'}
							{#if auth.grant === 'owner'}
								<div class="flex flex-none items-center gap-2">
									<button
										type="button"
										onclick={() => handleApprove(approval.id)}
										disabled={actingId === approval.id}
										class="rounded-md bg-agent-cyan px-2.5 py-1 text-xs font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
									>
										Approve
									</button>
									<button
										type="button"
										onclick={() => handleReject(approval.id)}
										disabled={actingId === approval.id}
										class="rounded-md border border-ink/15 px-2.5 py-1 text-xs text-ink hover:bg-neutral-200/60 disabled:opacity-50 dark:border-white/15 dark:text-ink-dark dark:hover:bg-white/5"
									>
										Reject
									</button>
								</div>
							{:else}
								<span class="flex-none text-xs text-neutral-500 italic">Owner access required</span>
							{/if}
						{:else}
							<span
								class="flex-none rounded-sm px-2 py-0.5 text-xs font-medium {approval.status ===
								'approved'
									? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
									: 'bg-neutral-200/60 text-neutral-700 dark:bg-white/10 dark:text-neutral-300'}"
							>
								{approval.status}
							</span>
						{/if}
					</div>
					{#if rowError[approval.id]}
						<p class="mt-2 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
							{rowError[approval.id]}
						</p>
					{/if}
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
