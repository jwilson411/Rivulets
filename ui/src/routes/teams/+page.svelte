<script lang="ts">
	import { teams, type TeamDetail } from '$lib/api/teams';
	import { agents, type Agent } from '$lib/api/agents';

	let teamList = $state<TeamDetail[]>([]);
	let agentList = $state<Agent[]>([]);
	let loadError = $state<string | null>(null);

	let newTeamName = $state('');
	let creating = $state(false);

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
		try {
			await teams.create(newTeamName.trim());
			newTeamName = '';
			await refresh();
		} finally {
			creating = false;
		}
	}

	async function toggleAgent(team: TeamDetail, agentId: string, checked: boolean) {
		const agentIds = checked
			? [...team.agent_ids, agentId]
			: team.agent_ids.filter((id) => id !== agentId);
		await teams.update(team.id, { agent_ids: agentIds });
		await refresh();
	}

	async function handleDelete(teamId: string) {
		await teams.remove(teamId);
		await refresh();
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Teams</h1>
		<p class="text-sm text-zinc-500">
			A team is a group of agents you assign to a channel as a unit (FR-2.2).
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex gap-2 rounded-md border border-zinc-200 p-4 dark:border-zinc-800"
	>
		<input
			type="text"
			bind:value={newTeamName}
			placeholder="Team name"
			class="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<button
			type="submit"
			disabled={creating}
			class="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			Create
		</button>
	</form>

	{#if loadError}
		<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else}
		<ul class="flex flex-col gap-3">
			{#each teamList as team (team.id)}
				<li class="rounded-md border border-zinc-200 p-4 dark:border-zinc-800">
					<div class="flex items-center justify-between">
						<p class="font-medium text-zinc-900 dark:text-zinc-50">{team.name}</p>
						<button
							onclick={() => handleDelete(team.id)}
							class="text-xs text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
						>
							Delete
						</button>
					</div>

					<div class="mt-3 flex flex-col gap-1 border-t border-zinc-100 pt-3 dark:border-zinc-900">
						<p class="mb-1 text-xs text-zinc-500">Agents on this team</p>
						{#if agentList.length === 0}
							<p class="text-xs text-zinc-400">No agents yet — create one under Agents.</p>
						{:else}
							{#each agentList as agent (agent.id)}
								<label class="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
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
			{/each}
		</ul>
	{/if}
</div>
