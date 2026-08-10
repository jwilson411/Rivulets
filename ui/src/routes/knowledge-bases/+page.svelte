<script lang="ts">
	import { resolve } from '$app/paths';
	import { knowledgeBases, type KnowledgeBase } from '$lib/api/knowledgeBases';
	import { agents as agentsApi, type Agent } from '$lib/api/agents';
	import { teams as teamsApi, type Team } from '$lib/api/teams';
	import FilterableList from '$lib/components/FilterableList.svelte';

	let kbList = $state<KnowledgeBase[]>([]);
	let agentList = $state<Agent[]>([]);
	let teamList = $state<Team[]>([]);
	let loadError = $state<string | null>(null);

	let newName = $state('');
	let newScopeType = $state<'agent' | 'team'>('agent');
	let newSubjectId = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let actionError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			const [kbs, agents, teams] = await Promise.all([
				knowledgeBases.list(),
				agentsApi.list(),
				teamsApi.list()
			]);
			kbList = kbs;
			agentList = agents;
			teamList = teams;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load knowledge bases';
		}
	}

	refresh();

	function subjectName(kb: KnowledgeBase): string {
		if (kb.scope_type === 'agent') {
			return agentList.find((a) => a.id === kb.agent_id)?.name ?? 'Unknown agent';
		}
		return teamList.find((t) => t.id === kb.team_id)?.name ?? 'Unknown team';
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim() || !newSubjectId) return;
		creating = true;
		createError = null;
		try {
			await knowledgeBases.create({
				name: newName.trim(),
				scope_type: newScopeType,
				agent_id: newScopeType === 'agent' ? newSubjectId : undefined,
				team_id: newScopeType === 'team' ? newSubjectId : undefined
			});
			newName = '';
			newSubjectId = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create knowledge base';
		} finally {
			creating = false;
		}
	}

	async function handleDelete(id: string) {
		actionError = null;
		try {
			await knowledgeBases.remove(id);
			await refresh();
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to delete knowledge base';
		}
	}
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Knowledge Bases</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			A knowledge base is a set of ingested documents an agent or team can search mid-conversation
			via the search_knowledge_base tool (#98). Requires an OpenAI provider configured under
			Providers, used to generate embeddings.
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-2 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<div class="flex gap-2">
			<input
				type="text"
				bind:value={newName}
				placeholder="Knowledge base name"
				class="flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
			<select
				aria-label="Scope"
				bind:value={newScopeType}
				onchange={() => (newSubjectId = '')}
				class="rounded-md border border-ink/15 bg-transparent px-2 py-2 text-sm text-ink dark:border-white/15 dark:text-ink-dark"
			>
				<option value="agent">Agent</option>
				<option value="team">Team</option>
			</select>
			<select
				aria-label="Subject"
				bind:value={newSubjectId}
				class="min-w-0 flex-1 rounded-md border border-ink/15 bg-transparent px-2 py-2 text-sm text-ink dark:border-white/15 dark:text-ink-dark"
			>
				<option value="">
					{newScopeType === 'agent' ? 'Choose an agent…' : 'Choose a team…'}
				</option>
				{#each newScopeType === 'agent' ? agentList : teamList as subject (subject.id)}
					<option value={subject.id}>{subject.name}</option>
				{/each}
			</select>
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
			items={kbList}
			getKey={(kb) => kb.id}
			searchPlaceholder="Search knowledge bases…"
			searchPredicate={(kb, q) => kb.name.toLowerCase().includes(q.toLowerCase())}
			emptyMessage="No knowledge bases yet — create one above."
			noMatchMessage="No knowledge bases match your search."
		>
			{#snippet item(kb)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					<div class="flex items-center justify-between">
						<a
							href={resolve('/knowledge-bases/[id]', { id: kb.id })}
							class="font-medium text-ink hover:underline dark:text-ink-dark"
						>
							{kb.name}
						</a>
						<button
							onclick={() => handleDelete(kb.id)}
							class="text-xs text-neutral-500 hover:text-agent-magenta-600"
						>
							Delete
						</button>
					</div>
					<p class="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
						{kb.scope_type === 'agent' ? 'Agent' : 'Team'}: {subjectName(kb)} · {kb.document_count}
						document{kb.document_count === 1 ? '' : 's'}
					</p>
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
