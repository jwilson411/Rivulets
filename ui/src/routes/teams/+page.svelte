<script lang="ts">
	import { teams, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent } from '$lib/api/agents';
	import { channels, type Channel } from '$lib/api/channels';
	import { auth } from '$lib/api/auth.svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { agentInk, INK_AVATAR } from '$lib/ink';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Teams (06-screens.md → Teams, mockup 2d): cards with member discs and
	// where each team is used; membership edits happen in a sheet with 48px
	// checkbox rows, saved as a batch.

	let teamList = $state<TeamDetail[]>([]);
	let agentList = $state<Agent[]>([]);
	let channelList = $state<Channel[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	// #393 (#353 leftover): which agents hold a capability scope. Delete
	// and membership writes 403 for an invite-grant session on a team that
	// holds one of these -- owners skip the fetch, they can always write.
	let scopedAgentIds = new SvelteSet<string>();

	let creating = $state(false);
	let newTeamName = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let editingTeam = $state<TeamDetail | null>(null);
	let memberDraft = $state<string[]>([]);
	let saveBusy = $state(false);
	let sheetError = $state<string | null>(null);
	let confirmingDelete = $state(false);

	function agentIsGated(agentId: string): boolean {
		return auth.grant !== 'owner' && scopedAgentIds.has(agentId);
	}

	function teamIsGated(team: TeamDetail): boolean {
		return auth.grant !== 'owner' && team.agent_ids.some((id) => scopedAgentIds.has(id));
	}

	function usedIn(team: TeamDetail): string {
		const names = channelList.filter((c) => c.team_id === team.id).map((c) => `#${c.name}`);
		return names.length ? `Used in ${names.join(', ')}` : 'Not used in any channel yet';
	}

	function membersOf(team: TeamDetail): Agent[] {
		return team.agent_ids
			.map((id) => agentList.find((a) => a.id === id))
			.filter((a): a is Agent => a !== undefined);
	}

	async function refresh() {
		loadError = null;
		try {
			const [teamSummaries, loadedAgents, loadedChannels] = await Promise.all([
				teams.list(),
				agents.list(),
				channels.list().catch(() => [] as Channel[])
			]);
			teamList = await Promise.all(teamSummaries.map((t) => teams.get(t.id)));
			agentList = loadedAgents;
			channelList = loadedChannels;
			if (auth.grant !== 'owner') {
				const gated = new SvelteSet<string>();
				await Promise.all(
					loadedAgents.map(async (agent) => {
						try {
							const out = await agents.getToolScopes(agent.id);
							if (out.scopes.length > 0) gated.add(agent.id);
						} catch {
							// Can't tell — hide the write that would 403 if it is gated.
							gated.add(agent.id);
						}
					})
				);
				scopedAgentIds.clear();
				for (const id of gated) scopedAgentIds.add(id);
			} else {
				scopedAgentIds.clear();
			}
		} catch {
			loadError = "Couldn't load teams.";
		} finally {
			loading = false;
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newTeamName.trim()) return;
		createBusy = true;
		createError = null;
		try {
			await teams.create(newTeamName.trim());
			newTeamName = '';
			creating = false;
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the team.";
		} finally {
			createBusy = false;
		}
	}

	function openTeam(team: TeamDetail) {
		editingTeam = team;
		memberDraft = [...team.agent_ids];
		sheetError = null;
		confirmingDelete = false;
	}

	function toggleMember(agentId: string) {
		if (agentIsGated(agentId)) return;
		memberDraft = memberDraft.includes(agentId)
			? memberDraft.filter((id) => id !== agentId)
			: [...memberDraft, agentId];
	}

	async function saveMembers() {
		if (!editingTeam) return;
		saveBusy = true;
		sheetError = null;
		try {
			await teams.update(editingTeam.id, { agent_ids: memberDraft });
			editingTeam = null;
			await refresh();
		} catch {
			sheetError = "Couldn't save the team. Try again.";
		} finally {
			saveBusy = false;
		}
	}

	async function handleDelete() {
		if (!editingTeam) return;
		saveBusy = true;
		sheetError = null;
		try {
			await teams.remove(editingTeam.id);
			editingTeam = null;
			await refresh();
		} catch {
			sheetError = "Couldn't delete the team. Try again.";
			confirmingDelete = false;
		} finally {
			saveBusy = false;
		}
	}
</script>

<div class="mx-auto max-w-[820px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Teams</h1>
		<Button onclick={() => (creating = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New team
		</Button>
	</div>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if teamList.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">No teams yet.</p>
	{:else}
		<div class="flex flex-col gap-4">
			{#each teamList as team (team.id)}
				<button
					type="button"
					onclick={() => openTeam(team)}
					class="rounded-2xl border border-line bg-surface px-6 py-5 text-left hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<span class="mb-3 block text-lg font-semibold text-ink dark:text-ink-dark">
						{team.name}
					</span>
					{#if membersOf(team).length > 0}
						<span class="mb-3 flex">
							{#each membersOf(team) as member, i (member.id)}
								<Disc
									name={member.name}
									colorClass={INK_AVATAR[agentInk(i)]}
									size={32}
									class="border-2 border-surface dark:border-surface-dark {i > 0 ? '-ml-2.5' : ''}"
								/>
							{/each}
						</span>
					{/if}
					<span class="block text-sm text-muted dark:text-muted-dark">{usedIn(team)}</span>
				</button>
			{/each}
		</div>
	{/if}
</div>

{#if creating}
	<Sheet title="New team" onClose={() => (creating = false)} width={480}>
		<form id="new-team-form" onsubmit={handleCreate} class="flex flex-col gap-2">
			<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-team-name">
				Name
			</label>
			<input
				id="new-team-name"
				type="text"
				bind:value={newTeamName}
				placeholder="Starter Team"
				class="h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
			/>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy || !newTeamName.trim()}
				onclick={() =>
					(document.getElementById('new-team-form') as HTMLFormElement).requestSubmit()}
			>
				Create team
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if editingTeam && !confirmingDelete}
	<Sheet title={editingTeam.name} onClose={() => (editingTeam = null)} width={480}>
		<div class="flex flex-col gap-2.5">
			<span class="text-sm font-semibold text-ink dark:text-ink-dark">Members</span>
			{#if agentList.length === 0}
				<p class="text-sm text-muted dark:text-muted-dark">
					No agents yet — create one under Agents.
				</p>
			{:else}
				{#each agentList as agent, i (agent.id)}
					<label
						class="flex h-12 cursor-pointer items-center gap-3 rounded-lg border border-line px-3.5 dark:border-line-dark {agentIsGated(
							agent.id
						)
							? 'opacity-50'
							: ''}"
					>
						<input
							type="checkbox"
							checked={memberDraft.includes(agent.id)}
							disabled={agentIsGated(agent.id)}
							onchange={() => toggleMember(agent.id)}
							class="accent-(--color-accent)"
						/>
						<Disc name={agent.name} colorClass={INK_AVATAR[agentInk(i)]} size={24} />
						<span class="text-[15px] font-medium text-ink dark:text-ink-dark">{agent.name}</span>
						<span class="ml-auto truncate text-[13px] text-muted dark:text-muted-dark">
							{agent.description}
						</span>
					</label>
				{/each}
			{/if}
		</div>
		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}
		{#snippet footer()}
			{#if !teamIsGated(editingTeam!)}
				<button
					type="button"
					onclick={() => (confirmingDelete = true)}
					class="mr-auto text-[15px] font-medium text-danger hover:underline"
				>
					Delete team
				</button>
			{/if}
			<Button variant="secondary" onclick={() => (editingTeam = null)}>Cancel</Button>
			<Button onclick={saveMembers} disabled={saveBusy}>
				{saveBusy ? 'Saving…' : 'Save'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if editingTeam && confirmingDelete}
	<Sheet title="Delete {editingTeam.name}?" onClose={() => (confirmingDelete = false)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Channels using this team will stop routing to agents until you assign another team.
		</p>
		{#if sheetError}
			<p class="text-sm text-danger">{sheetError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingDelete = false)}>Cancel</Button>
			<Button variant="destructive" onclick={handleDelete} disabled={saveBusy}>
				{saveBusy ? 'Deleting…' : 'Delete team'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
