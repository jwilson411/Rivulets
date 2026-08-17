<script lang="ts">
	import { approvals, type PendingApproval } from '$lib/api/approvals';
	import { auth } from '$lib/api/auth.svelte';
	import { approvalsBadge } from '$lib/approvalsBadge.svelte';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Approvals (06-screens.md → Approvals, mockup 1h): one inbox for
	// anything that needs a human's OK — an agent-created schedule (#93), a
	// tripped spend cap (#97), or blocked unattended tool use (#100).
	// Guests see the list read-only (2q); deciding is owner-only.

	type StatusFilter = 'waiting' | 'done' | 'all';
	type SourceFilter = PendingApproval['source_type'] | null;

	const sourceLabels: Record<PendingApproval['source_type'], string> = {
		schedule: 'Schedule',
		budget: 'Spend',
		tool_guardrail: 'Tools'
	};

	let approvalList = $state<PendingApproval[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let rowError = $state<Record<string, string | null>>({});
	let actingId = $state<string | null>(null);
	let statusFilter = $state<StatusFilter>('waiting');
	let sourceFilter = $state<SourceFilter>(null);

	let waitingCount = $derived(approvalList.filter((a) => a.status === 'pending').length);
	let visible = $derived(
		approvalList.filter((a) => {
			if (statusFilter === 'waiting' && a.status !== 'pending') return false;
			if (statusFilter === 'done' && a.status === 'pending') return false;
			if (sourceFilter && a.source_type !== sourceFilter) return false;
			return true;
		})
	);

	async function refresh() {
		loadError = null;
		try {
			approvalList = await approvals.list();
		} catch {
			loadError = "Couldn't load approvals.";
		} finally {
			loading = false;
		}
	}

	refresh();

	async function decide(id: string, action: 'approve' | 'reject') {
		rowError[id] = null;
		actingId = id;
		try {
			if (action === 'approve') await approvals.approve(id);
			else await approvals.reject(id);
			await refresh();
			await approvalsBadge.refresh();
		} catch {
			rowError[id] = action === 'approve' ? "Couldn't approve that." : "Couldn't reject that.";
		} finally {
			actingId = null;
		}
	}
</script>

<div class="mx-auto max-w-[820px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<h1 class="mb-5 font-display text-[32px] font-semibold text-ink dark:text-ink-dark">Needs you</h1>

	<div class="mb-7 flex flex-wrap gap-2">
		<FilterChip selected={statusFilter === 'waiting'} onclick={() => (statusFilter = 'waiting')}>
			Waiting{waitingCount ? ` · ${waitingCount}` : ''}
		</FilterChip>
		<FilterChip selected={statusFilter === 'done'} onclick={() => (statusFilter = 'done')}>
			Done
		</FilterChip>
		<FilterChip selected={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>
			All
		</FilterChip>
		<span class="mx-1 hidden w-px self-stretch bg-line sm:block dark:bg-line-dark"></span>
		{#each Object.entries(sourceLabels) as [value, label] (value)}
			<FilterChip
				selected={sourceFilter === value}
				onclick={() =>
					(sourceFilter =
						sourceFilter === value ? null : (value as PendingApproval['source_type']))}
			>
				{label}
			</FilterChip>
		{/each}
	</div>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if visible.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			{statusFilter === 'waiting' ? "You're clear. Nothing is waiting." : 'Nothing here yet.'}
		</p>
	{:else}
		<div class="flex flex-col gap-4">
			{#each visible as approval (approval.id)}
				<div
					class="rounded-2xl border border-line bg-surface px-7 py-6 dark:border-line-dark dark:bg-surface-dark"
				>
					<div class="mb-2 flex items-center gap-2.5">
						<span class="text-sm font-semibold text-ink dark:text-ink-dark">{approval.title}</span>
						<StatusPill tone={approval.source_type === 'budget' ? 'danger' : 'warn'}>
							{sourceLabels[approval.source_type]}
						</StatusPill>
					</div>
					<p class="mb-5 text-base leading-normal text-ink dark:text-ink-dark">
						{approval.detail}
					</p>
					{#if approval.status === 'pending'}
						{#if auth.grant === 'owner'}
							<div class="flex items-center justify-between gap-3">
								<Button
									variant="destructive"
									disabled={actingId === approval.id}
									onclick={() => decide(approval.id, 'reject')}
								>
									Reject
								</Button>
								<Button
									disabled={actingId === approval.id}
									class="px-8"
									onclick={() => decide(approval.id, 'approve')}
								>
									Approve
								</Button>
							</div>
						{:else}
							<p class="text-[15px] text-muted italic dark:text-muted-dark">
								Only the workspace owner can approve.
							</p>
						{/if}
					{:else}
						<StatusPill tone={approval.status === 'approved' ? 'accent' : 'neutral'}>
							{approval.status === 'approved' ? 'Approved' : 'Rejected'}
						</StatusPill>
					{/if}
					{#if rowError[approval.id]}
						<p class="mt-3 text-sm text-danger">{rowError[approval.id]}</p>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
