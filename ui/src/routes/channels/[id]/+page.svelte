<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { channels, type Channel } from '$lib/api/channels';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { teams, type Team, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent } from '$lib/api/agents';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import { runs, type RunTrace } from '$lib/api/runs';
	import { files as filesApi } from '$lib/api/files';
	import { agentInk, INK_AVATAR } from '$lib/ink';
	import { formatClock } from '$lib/format';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StreamBar from '$lib/ui/StreamBar.svelte';

	// Channel page (06-screens.md → Channel, mockup 1f): a room of thread
	// cards. Posting here CREATES a new conversation (rivulet) — replies
	// live inside each card's own page. There is no channel-root transcript.

	interface ThreadPreview {
		title: string;
		preview: string;
		lastSender: string | null;
		lastAt: string | null;
	}

	let channel = $state<Channel | null>(null);
	let rivuletList = $state<Rivulet[]>([]);
	let previews = $state<Record<string, ThreadPreview>>({});
	let teamList = $state<Team[]>([]);
	let teamMembers = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let latestRunByRivulet = $state<Record<string, RunTrace>>({});
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let teamMenuOpen = $state(false);
	let teamChangeError = $state<string | null>(null);
	let posting = $state(false);
	let postError = $state<string | null>(null);

	let routedTeam = $derived(teamList.find((t) => t.id === channel?.team_id) ?? null);
	let agentsNotReady = $derived(
		teamMembers.length > 0 && teamMembers.every((a) => !a.agentos_agent_id)
	);
	let helper = $derived(
		agentsNotReady
			? "Agents aren't ready to run — sign out and back in"
			: routedTeam
				? `Routes to ${routedTeam.name}`
				: "No team — agents won't answer"
	);

	async function loadTeamMembers(teamId: string | null) {
		teamMembers = [];
		if (!teamId) return;
		try {
			const [detail, agentList] = await Promise.all([teams.get(teamId), agents.list()]);
			teamMembers = (detail as TeamDetail).agent_ids
				.map((id) => agentList.find((a) => a.id === id))
				.filter((a): a is Agent => a !== undefined);
		} catch {
			// Member discs are decoration on the chip — fine to omit.
		}
	}

	async function loadPreviews(list: Rivulet[]) {
		const entries = await Promise.all(
			list.map(async (r) => {
				const msgs = await rivulets.listMessages(r.id).catch(() => [] as Message[]);
				const spoken = msgs.filter((m) => m.content_type === 'text');
				const root = spoken.find((m) => m.sender_type === 'human');
				const last = spoken.at(-1) ?? null;
				const preview: ThreadPreview = {
					title: r.title || root?.content || 'Conversation',
					preview: last && last !== root ? last.content : '',
					lastSender: last?.sender_name ?? null,
					lastAt: last?.created_at ?? null
				};
				return [r.id, preview] as const;
			})
		);
		previews = Object.fromEntries(entries);
	}

	async function load(channelId: string) {
		loading = true;
		loadError = null;
		try {
			const [loadedChannel, loadedRivulets, loadedTeams, loadedWorkflows, loadedRuns] =
				await Promise.all([
					channels.get(channelId),
					rivulets.listForChannel(channelId),
					teams.list().catch(() => [] as Team[]),
					workflows.list().catch(() => [] as Workflow[]),
					runs.list({ channelId, limit: 200 }).catch(() => [] as RunTrace[])
				]);
			channel = loadedChannel;
			rivuletList = [...loadedRivulets].sort((a, b) => b.created_at.localeCompare(a.created_at));
			teamList = loadedTeams;
			workflowList = loadedWorkflows;
			const latest: Record<string, RunTrace> = {};
			for (const trace of loadedRuns) {
				if (trace.rivulet_id && !latest[trace.rivulet_id]) latest[trace.rivulet_id] = trace;
			}
			latestRunByRivulet = latest;
			await Promise.all([loadPreviews(rivuletList), loadTeamMembers(loadedChannel.team_id)]);
		} catch {
			loadError = "Couldn't load conversations.";
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		load(page.params.id!);
	});

	async function handleTeamChange(teamId: string | null) {
		const channelId = page.params.id!;
		teamMenuOpen = false;
		teamChangeError = null;
		try {
			channel = await channels.update(channelId, { team_id: teamId });
			await loadTeamMembers(teamId);
		} catch {
			teamChangeError = "Couldn't change the team. Try again.";
		}
	}

	async function handlePost(text: string, files: File[]): Promise<boolean> {
		const channelId = page.params.id!;
		posting = true;
		postError = null;
		try {
			const uploaded = await Promise.all(files.map((f) => filesApi.upload(f)));
			const created = await rivulets.create(
				channelId,
				text,
				uploaded.map((f) => f.file_id)
			);
			goto(
				resolve('/channels/[id]/rivulets/[rivuletId]', { id: channelId, rivuletId: created.id })
			);
			return true;
		} catch {
			postError = "Couldn't send that. Try again.";
			return false;
		} finally {
			posting = false;
		}
	}

	function dotClass(rivulet: Rivulet, index: number): string {
		const latest = latestRunByRivulet[rivulet.id];
		if (latest?.status === 'error') return 'bg-danger';
		if (latest?.status === 'running' || rivulet.status === 'paused') return 'bg-warn';
		if (index === 0) return 'breath bg-accent dark:bg-accent-dark';
		return 'bg-line dark:bg-line-dark';
	}

	function latestRunNote(rivuletId: string): string | null {
		const latest = latestRunByRivulet[rivuletId];
		if (!latest) return null;
		if (latest.status === 'running') {
			return latest.span_count === 0
				? 'Last run is stuck — no steps recorded.'
				: 'A run is still in progress.';
		}
		if (latest.status === 'error') return 'Last run failed.';
		if (latest.status === 'cancelled') return 'Last run was cancelled.';
		return null;
	}
</script>

<div class="flex h-full flex-col">
	<header
		class="flex flex-wrap items-start gap-4 border-b border-line px-4 pt-8 pb-5 md:px-10 dark:border-line-dark"
	>
		<div class="min-w-0">
			<h1 class="mb-1 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">
				#{channel?.name ?? '…'}
			</h1>
			<div class="text-sm text-muted dark:text-muted-dark">
				{rivuletList.length} conversation{rivuletList.length === 1 ? '' : 's'}{channel?.description
					? ` · ${channel.description}`
					: ''}
			</div>
		</div>
		<div class="relative ml-auto flex-none">
			<button
				type="button"
				onclick={() => (teamMenuOpen = !teamMenuOpen)}
				aria-expanded={teamMenuOpen}
				class="flex h-10 items-center gap-2.5 rounded-lg border border-line bg-surface px-3.5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
			>
				{#if teamMembers.length > 0}
					<span class="flex">
						{#each teamMembers.slice(0, 4) as member, i (member.id)}
							<Disc
								name={member.name}
								colorClass={INK_AVATAR[agentInk(i)]}
								size={24}
								class="border-2 border-surface dark:border-surface-dark {i > 0 ? '-ml-2' : ''}"
							/>
						{/each}
					</span>
				{/if}
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">
					{routedTeam?.name ?? 'No team'}
				</span>
				<Icon name="chevron-down" class="h-3.5 w-3.5 text-muted dark:text-muted-dark" />
			</button>
			{#if teamMenuOpen}
				<div
					class="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-line bg-surface p-2 shadow-pop dark:border-line-dark dark:bg-surface-dark"
					role="menu"
				>
					{#each teamList as team (team.id)}
						<button
							type="button"
							role="menuitem"
							onclick={() => handleTeamChange(team.id)}
							class="flex h-11 w-full items-center rounded-md px-3 text-left text-[15px] {team.id ===
							channel?.team_id
								? 'bg-accent-soft font-semibold dark:bg-accent-soft-dark'
								: 'hover:bg-paper dark:hover:bg-paper-dark'} text-ink dark:text-ink-dark"
						>
							{team.name}
						</button>
					{/each}
					<button
						type="button"
						role="menuitem"
						onclick={() => handleTeamChange(null)}
						class="flex h-11 w-full items-center rounded-md px-3 text-left text-[15px] {!channel?.team_id
							? 'bg-accent-soft font-semibold dark:bg-accent-soft-dark'
							: 'hover:bg-paper dark:hover:bg-paper-dark'} text-muted dark:text-muted-dark"
					>
						No team — agents won't answer
					</button>
				</div>
			{/if}
		</div>
		{#if teamChangeError}
			<p class="w-full text-sm text-danger">{teamChangeError}</p>
		{/if}
	</header>

	<div class="flex-1 overflow-y-auto px-4 py-6 md:px-10">
		{#if agentsNotReady}
			<ErrorBanner
				class="mb-4"
				message="Agents aren't ready to run on this node. Sign out and back in, or check Settings > Providers."
				onRetry={() => load(page.params.id!)}
			/>
		{/if}
		{#if loading}
			<SkeletonCards count={3} />
		{:else if loadError}
			<ErrorBanner message={loadError} onRetry={() => load(page.params.id!)} />
		{:else if rivuletList.length === 0}
			<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
				Start the first conversation in #{channel?.name ?? 'this channel'}.
			</p>
		{:else}
			<div class="flex flex-col gap-4">
				{#each rivuletList as rivulet, i (rivulet.id)}
					{@const preview = previews[rivulet.id]}
					{@const runNote = latestRunNote(rivulet.id)}
					<a
						href={resolve('/channels/[id]/rivulets/[rivuletId]', {
							id: page.params.id!,
							rivuletId: rivulet.id
						})}
						class="flex min-h-[88px] gap-4 rounded-2xl border border-line bg-surface px-6 py-5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
					>
						<span class="mt-2 h-2 w-2 flex-none rounded-full {dotClass(rivulet, i)}"></span>
						<span class="min-w-0">
							<span
								class="mb-1 block truncate text-base leading-snug font-semibold text-ink dark:text-ink-dark"
							>
								{preview?.title ?? '…'}
							</span>
							{#if runNote}
								<span
									class="block truncate text-[15px] {latestRunByRivulet[rivulet.id]?.status ===
									'error'
										? 'text-danger'
										: 'text-warn'}"
								>
									{runNote}
								</span>
							{:else if rivulet.status === 'paused'}
								<span class="block truncate text-[15px] text-muted dark:text-muted-dark">
									Paused — waiting on a person.
								</span>
							{:else if preview?.preview}
								<span class="block truncate text-[15px] text-muted dark:text-muted-dark">
									{preview.preview}
								</span>
							{/if}
							{#if preview?.lastSender}
								<span class="mt-2 block text-[13px] text-muted dark:text-muted-dark">
									{preview.lastSender}{preview.lastAt ? ` · ${formatClock(preview.lastAt)}` : ''}
								</span>
							{/if}
						</span>
					</a>
				{/each}
			</div>
		{/if}
	</div>

	<div class="px-4 pb-24 md:px-10 md:pb-7">
		<StreamBar
			placeholder="Start a conversation…"
			{helper}
			busy={posting}
			error={postError}
			slashWorkflows={workflowList}
			onSend={handlePost}
		/>
	</div>
</div>
