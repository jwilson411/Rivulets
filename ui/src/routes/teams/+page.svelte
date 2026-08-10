<script lang="ts">
	import { teams, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent } from '$lib/api/agents';
	import FilterableList from '$lib/components/FilterableList.svelte';

	let teamList = $state<TeamDetail[]>([]);
	let agentList = $state<Agent[]>([]);
	let loadError = $state<string | null>(null);

	let newTeamName = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let actionError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			const [teamSummaries, loadedAgents] = await Promise.all([teams.list(), agents.list()]);
			teamList = await Promise.all(teamSummaries.map((t) => teams.get(t.id)));
			agentList = loadedAgents;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load teams';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newTeamName.trim()) return;
		creating = true;
		createError = null;
		try {
			await teams.create(newTeamName.trim());
			newTeamName = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create team';
		} finally {
			creating = false;
		}
	}

	async function toggleAgent(team: TeamDetail, agentId: string, checked: boolean) {
		const agentIds = checked
			? [...team.agent_ids, agentId]
			: team.agent_ids.filter((id) => id !== agentId);
		actionError = null;
		try {
			await teams.update(team.id, { agent_ids: agentIds });
			await refresh();
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to update team';
		}
	}

	async function handleDelete(teamId: string) {
		actionError = null;
		try {
			await teams.remove(teamId);
			await refresh();
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to delete team';
		}
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Teams</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			A team is a group of agents you assign to a channel as a unit (FR-2.2).
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-2 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<div class="flex gap-2">
			<input
				type="text"
				bind:value={newTeamName}
				placeholder="Team name"
				class="flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<button
				type="submit"
				disabled={creating}
				class="rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
			>
				Create
			</button>
		</div>
		{#if createError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else}
		{#if actionError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{actionError}</p>
		{/if}
		<FilterableList
			items={teamList}
			getKey={(team) => team.id}
			searchPlaceholder="Search teams…"
			searchPredicate={(team, q) => team.name.toLowerCase().includes(q.toLowerCase())}
			emptyMessage="No teams yet — create one above."
			noMatchMessage="No teams match your search."
		>
			{#snippet item(team)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					<div class="flex items-center justify-between">
						<p class="font-medium text-ink dark:text-ink-dark">{team.name}</p>
						<button
							onclick={() => handleDelete(team.id)}
							class="text-xs text-neutral-500 hover:text-agent-magenta-600"
						>
							Delete
						</button>
					</div>

					<div class="mt-3 flex flex-col gap-1 border-t border-ink/10 pt-3 dark:border-white/10">
						<p class="mb-1 text-xs text-neutral-600 dark:text-neutral-400">Agents on this team</p>
						{#if agentList.length === 0}
							<p class="text-xs text-neutral-500">No agents yet — create one under Agents.</p>
						{:else}
							{#each agentList as agent (agent.id)}
								<label class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark">
									<input
										type="checkbox"
										checked={team.agent_ids.includes(agent.id)}
										onchange={(e) =>
											toggleAgent(team, agent.id, (e.target as HTMLInputElement).checked)}
									/>
									{agent.name}
								</label>
							{/each}
						{/if}
					</div>
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
