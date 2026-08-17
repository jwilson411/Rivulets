<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { channels, type Channel } from '$lib/api/channels';
	import { rivulets, type Rivulet, type Message } from '$lib/api/rivulets';
	import { teams, type Team, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent, type RoutingRule } from '$lib/api/agents';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import { runs, type RunTrace } from '$lib/api/runs';
	import { files as filesApi } from '$lib/api/files';
	import { agentInk, INK_AVATAR } from '$lib/ink';
	import { formatClock } from '$lib/format';
	import type { MentionCandidate } from '$lib/mentions';
	import { teamComposerHint, teamSpeakSummary } from '$lib/teamRouting';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import FilterChip from '$lib/ui/FilterChip.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StreamBar from '$lib/ui/StreamBar.svelte';

	// Channel page (06-screens.md → Channel, mockup 1f): a room of thread
	// cards. Posting here CREATES a new conversation (rivulet) unless the
	// composer is set to continue the last one (#412) — replies live
	// inside each card's own page. There is no channel-root transcript.
	// Closed rivulets hide behind Archived; DELETE is a soft archive.

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
	let memberRules = $state<Record<string, RoutingRule[]>>({});
	let workflowList = $state<Workflow[]>([]);
	let latestRunByRivulet = $state<Record<string, RunTrace>>({});
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let teamMenuOpen = $state(false);
	let teamChangeError = $state<string | null>(null);
	let posting = $state(false);
	let postError = $state<string | null>(null);
	let showArchived = $state(false);
	let continueLast = $state(false);
	let archiving = $state<Rivulet | null>(null);
	let archiveBusy = $state(false);
	let archiveError = $state<string | null>(null);
	let listError = $state<string | null>(null);

	let routedTeam = $derived(teamList.find((t) => t.id === channel?.team_id) ?? null);
	let agentsNotReady = $derived(
		teamMembers.length > 0 && teamMembers.every((a) => !a.agentos_agent_id)
	);
	let helper = $derived(
		agentsNotReady
			? "Agents aren't ready to run — sign out and back in"
			: routedTeam
				? teamComposerHint(routedTeam.name)
				: "No team — agents won't answer"
	);
	let speakSummary = $derived(
		!agentsNotReady && teamMembers.length > 0
			? teamSpeakSummary(
					teamMembers.map((member) => ({
						name: member.name,
						rules: memberRules[member.id] ?? []
					}))
				)
			: ''
	);
	let mentionCandidates = $derived<MentionCandidate[]>(
		teamMembers.map((member) => ({ id: member.id, name: member.name, kind: 'agent' }))
	);
	// Header chip keeps four member discs; leftover members become a +N
	// disc so a fifth agent is not silently dropped (#423).
	const TEAM_CHIP_VISIBLE = 4;
	let visibleTeamMembers = $derived(teamMembers.slice(0, TEAM_CHIP_VISIBLE));
	let overflowTeamMembers = $derived(teamMembers.slice(TEAM_CHIP_VISIBLE));
	let overflowCount = $derived(overflowTeamMembers.length);
	let allMemberNames = $derived(teamMembers.map((member) => member.name).join(', '));
	let overflowNames = $derived(overflowTeamMembers.map((member) => member.name).join(', '));
	let composer = $state<{ insertMention: (name: string) => void } | null>(null);
	let openRivulets = $derived(rivuletList.filter((r) => r.status !== 'closed'));
	let closedRivulets = $derived(rivuletList.filter((r) => r.status === 'closed'));
	let visibleRivulets = $derived(showArchived ? closedRivulets : openRivulets);
	let lastOpen = $derived(openRivulets[0] ?? null);
	let composerPlaceholder = $derived(
		continueLast && lastOpen ? 'Reply to the last conversation…' : 'Start a conversation…'
	);

	async function loadTeamMembers(teamId: string | null) {
		teamMembers = [];
		memberRules = {};
		if (!teamId) return;
		try {
			const [detail, agentList] = await Promise.all([teams.get(teamId), agents.list()]);
			teamMembers = (detail as TeamDetail).agent_ids
				.map((id) => agentList.find((a) => a.id === id))
				.filter((a): a is Agent => a !== undefined);
			const ruleEntries = await Promise.all(
				teamMembers.map(async (member) => {
					const rules = await agents.getRoutingRules(member.id).catch(() => [] as RoutingRule[]);
					return [member.id, rules] as const;
				})
			);
			memberRules = Object.fromEntries(ruleEntries);
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
			const fileIds = uploaded.map((f) => f.file_id);
			if (continueLast && lastOpen) {
				await rivulets.postMessage(lastOpen.id, text, fileIds);
				goto(
					resolve('/channels/[id]/rivulets/[rivuletId]', {
						id: channelId,
						rivuletId: lastOpen.id
					})
				);
				return true;
			}
			// #413: create returns as soon as the human message is committed;
			// dispatch continues in the background. The rivulet page picks up
			// Routing… from the still-running trace / SSE.
			const created = await rivulets.create(channelId, text, fileIds);
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

	async function handleArchive() {
		if (!archiving) return;
		archiveBusy = true;
		archiveError = null;
		try {
			await rivulets.close(archiving.id);
			rivuletList = rivuletList.map((r) =>
				r.id === archiving!.id ? { ...r, status: 'closed' } : r
			);
			archiving = null;
		} catch {
			archiveError = "Couldn't archive that conversation. Try again.";
		} finally {
			archiveBusy = false;
		}
	}

	async function handleUnarchive(rivulet: Rivulet) {
		listError = null;
		try {
			const updated = await rivulets.resume(rivulet.id);
			rivuletList = rivuletList.map((r) => (r.id === rivulet.id ? updated : r));
		} catch {
			listError = "Couldn't unarchive that conversation. Try again.";
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
		if (latest.status === 'completed' && latest.total_tokens === 0 && latest.span_count <= 1) {
			return 'Nobody picked this up.';
		}
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
				{openRivulets.length} conversation{openRivulets.length === 1
					? ''
					: 's'}{channel?.description ? ` · ${channel.description}` : ''}
			</div>
			<p class="mt-1.5 text-sm text-muted dark:text-muted-dark">
				Each send starts a conversation — click a card to reply.
			</p>
		</div>
		<div class="relative ml-auto flex max-w-full flex-none flex-col items-end gap-1.5">
			<div
				class="flex h-10 items-center rounded-lg border border-line bg-surface hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
			>
				{#if teamMembers.length > 0}
					<span class="flex pl-2" title={allMemberNames}>
						{#each visibleTeamMembers as member, i (member.id)}
							<button
								type="button"
								title="Mention {member.name}"
								aria-label="Mention {member.name}"
								onclick={() => composer?.insertMention(member.name)}
								class="flex items-center {i > 0 ? '-ml-2' : ''}"
							>
								<Disc
									name={member.name}
									colorClass={INK_AVATAR[agentInk(i)]}
									size={24}
									class="border-2 border-surface dark:border-surface-dark"
								/>
							</button>
						{/each}
						{#if overflowCount > 0}
							<button
								type="button"
								title="+{overflowCount} more: {overflowNames}"
								aria-label="+{overflowCount} more: {overflowNames}"
								onclick={() => (teamMenuOpen = !teamMenuOpen)}
								class="-ml-2 flex items-center"
							>
								<span
									class="inline-flex h-6 w-6 flex-none items-center justify-center rounded-[8px] border-2 border-surface bg-paper text-[11px] font-semibold text-ink dark:border-surface-dark dark:bg-paper-dark dark:text-ink-dark"
								>
									+{overflowCount}
								</span>
							</button>
						{/if}
					</span>
				{/if}
				<button
					type="button"
					onclick={() => (teamMenuOpen = !teamMenuOpen)}
					aria-expanded={teamMenuOpen}
					class="flex h-10 items-center gap-2.5 px-3.5"
				>
					<span class="text-sm font-semibold text-ink dark:text-ink-dark">
						{routedTeam?.name ?? 'No team'}
					</span>
					<Icon name="chevron-down" class="h-3.5 w-3.5 text-muted dark:text-muted-dark" />
				</button>
			</div>
			{#if teamMenuOpen}
				<div
					class="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-line bg-surface p-2 shadow-pop dark:border-line-dark dark:bg-surface-dark"
					role="menu"
				>
					{#if teamMembers.length > 0}
						<p
							class="px-3 pt-1 pb-1 text-[11px] font-semibold tracking-wide text-muted uppercase dark:text-muted-dark"
						>
							On this team
						</p>
						{#each teamMembers as member (member.id)}
							<button
								type="button"
								role="menuitem"
								onclick={() => {
									composer?.insertMention(member.name);
									teamMenuOpen = false;
								}}
								class="flex h-11 w-full items-center rounded-md px-3 text-left text-[15px] text-ink hover:bg-paper dark:text-ink-dark dark:hover:bg-paper-dark"
							>
								@{member.name}
							</button>
						{/each}
						<div class="my-1 border-t border-line dark:border-line-dark"></div>
					{/if}
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
			{#if speakSummary}
				<p class="max-w-[28rem] text-right text-[13px] text-muted dark:text-muted-dark">
					When to speak: {speakSummary}
				</p>
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
		{#if !loading && !loadError && (openRivulets.length > 0 || closedRivulets.length > 0)}
			<div class="mb-4 flex gap-2">
				<FilterChip selected={!showArchived} onclick={() => (showArchived = false)}>
					Active
				</FilterChip>
				<FilterChip selected={showArchived} onclick={() => (showArchived = true)}>
					Archived
				</FilterChip>
			</div>
		{/if}
		{#if listError}
			<ErrorBanner class="mb-4" message={listError} />
		{/if}
		{#if loading}
			<SkeletonCards count={3} />
		{:else if loadError}
			<ErrorBanner message={loadError} onRetry={() => load(page.params.id!)} />
		{:else if visibleRivulets.length === 0}
			<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
				{#if showArchived}
					No archived conversations.
				{:else}
					Each send starts a conversation — click a card to reply.
				{/if}
			</p>
		{:else}
			<div class="flex flex-col gap-4">
				{#each visibleRivulets as rivulet, i (rivulet.id)}
					{@const preview = previews[rivulet.id]}
					{@const runNote = latestRunNote(rivulet.id)}
					<div
						class="flex min-h-[88px] gap-4 rounded-2xl border border-line bg-surface px-6 py-5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
					>
						<a
							href={resolve('/channels/[id]/rivulets/[rivuletId]', {
								id: page.params.id!,
								rivuletId: rivulet.id
							})}
							class="flex min-w-0 flex-1 gap-4"
						>
							<span class="mt-2 h-2 w-2 flex-none rounded-full {dotClass(rivulet, i)}"></span>
							<span class="min-w-0">
								<span
									class="mb-1 block truncate text-base leading-snug font-semibold text-ink dark:text-ink-dark"
								>
									{preview?.title ?? '…'}
								</span>
								{#if rivulet.status === 'closed'}
									<span class="block truncate text-[15px] text-muted dark:text-muted-dark">
										Archived
									</span>
								{:else if runNote}
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
						{#if rivulet.status === 'closed'}
							<button
								type="button"
								onclick={() => handleUnarchive(rivulet)}
								class="self-center text-sm font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
							>
								Unarchive
							</button>
						{:else}
							<button
								type="button"
								onclick={() => (archiving = rivulet)}
								class="self-center text-sm font-semibold text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
							>
								Archive
							</button>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<div class="px-4 pb-24 md:px-10 md:pb-7">
		{#if lastOpen}
			<div class="mb-3 flex flex-wrap gap-2">
				<FilterChip selected={!continueLast} onclick={() => (continueLast = false)}>
					New conversation
				</FilterChip>
				<FilterChip selected={continueLast} onclick={() => (continueLast = true)}>
					Continue last
				</FilterChip>
			</div>
		{/if}
		{#if posting}
			<div class="mb-3 flex items-center gap-2 pl-1 text-sm">
				<span
					class="flex h-6 items-center gap-1.5 rounded-full bg-accent-soft px-2.5 text-[13px] font-semibold text-accent dark:bg-accent-soft-dark dark:text-accent-dark"
				>
					<span class="breath h-1.5 w-1.5 rounded-full bg-current"></span>
					Routing…
				</span>
			</div>
		{/if}
		<StreamBar
			bind:this={composer}
			placeholder={composerPlaceholder}
			{helper}
			busy={posting}
			error={postError}
			slashWorkflows={workflowList}
			{mentionCandidates}
			onSend={handlePost}
		/>
	</div>
</div>

{#if archiving}
	<Sheet title="Archive this conversation?" onClose={() => (archiving = null)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			It leaves the channel list. You can find it under Archived and restore it.
		</p>
		{#if archiveError}
			<p class="text-sm text-danger">{archiveError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (archiving = null)}>Cancel</Button>
			<Button variant="destructive" onclick={handleArchive} disabled={archiveBusy}>
				{archiveBusy ? 'Archiving…' : 'Archive'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
