<script lang="ts">
	import { agents, type Agent, type RoutingRule, type RuleType } from '$lib/api/agents';
	import { providers as providersApi, type Provider } from '$lib/api/providers';
	import AgentForm, { type AgentFormValues } from '$lib/components/AgentForm.svelte';

	let agentList = $state<Agent[]>([]);
	let providerList = $state<Provider[]>([]);
	let rulesByAgent = $state<Record<string, RoutingRule[]>>({});
	let loadError = $state<string | null>(null);

	let creating = $state(false);
	let createError = $state<string | null>(null);
	let createFormKey = $state(0);

	let editingAgentId = $state<string | null>(null);
	let updating = $state(false);
	let updateError = $state<string | null>(null);

	let keywordDrafts = $state<Record<string, string>>({});
	let actionError = $state<string | null>(null);

	let peerPreferenceDrafts = $state<Record<string, string>>({});

	async function refresh() {
		loadError = null;
		try {
			[agentList, providerList] = await Promise.all([agents.list(), providersApi.list()]);
			const entries = await Promise.all(
				agentList.map(async (a) => [a.id, await agents.getRoutingRules(a.id)] as const)
			);
			rulesByAgent = Object.fromEntries(entries);
			const preferenceEntries = await Promise.all(
				agentList.map(
					async (a) => [a.id, (await agents.getPeerPreference(a.id)).capability_tag ?? ''] as const
				)
			);
			peerPreferenceDrafts = Object.fromEntries(preferenceEntries);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load agents';
		}
	}

	refresh();

	async function handleCreate(values: AgentFormValues) {
		createError = null;
		creating = true;
		try {
			await agents.create(values);
			createFormKey += 1; // remounts AgentForm so its fields reset
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create agent';
		} finally {
			creating = false;
		}
	}

	function startEdit(agentId: string) {
		updateError = null;
		editingAgentId = agentId;
	}

	function cancelEdit() {
		editingAgentId = null;
		updateError = null;
	}

	async function handleUpdate(agentId: string, values: AgentFormValues) {
		updateError = null;
		updating = true;
		try {
			await agents.update(agentId, values);
			editingAgentId = null;
			await refresh();
		} catch (err) {
			updateError = err instanceof Error ? err.message : 'Failed to update agent';
		} finally {
			updating = false;
		}
	}

	async function setRule(agentId: string, ruleType: RuleType, pattern: string) {
		actionError = null;
		try {
			await agents.setRoutingRules(agentId, [{ rule_type: ruleType, pattern, priority: 10 }]);
			rulesByAgent[agentId] = await agents.getRoutingRules(agentId);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to update routing rule';
		}
	}

	async function setKeywordRule(agentId: string) {
		const raw = keywordDrafts[agentId]?.trim();
		if (!raw) return;
		const keywords = raw
			.split(',')
			.map((k) => k.trim())
			.filter(Boolean);
		await setRule(agentId, 'keyword', JSON.stringify(keywords));
		keywordDrafts[agentId] = '';
	}

	async function savePeerPreference(agentId: string) {
		actionError = null;
		const tag = peerPreferenceDrafts[agentId]?.trim() || null;
		try {
			await agents.setPeerPreference(agentId, tag);
			peerPreferenceDrafts[agentId] = tag ?? '';
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to update peer preference';
		}
	}

	function ruleSummary(rules: RoutingRule[] | undefined): string {
		if (!rules || rules.length === 0) return 'No routing rules — only @mention triggers this agent';
		return rules
			.map((r) => (r.rule_type === 'keyword' ? `keyword: ${r.pattern}` : r.rule_type))
			.join(', ');
	}

	async function handleDelete(agentId: string) {
		actionError = null;
		try {
			await agents.remove(agentId);
			await refresh();
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to delete agent';
		}
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Agents</h1>
		<p class="text-sm text-neutral-600 dark:text-neutral-400">
			Agents you create here register with AgentOS automatically (FR-3.2).
		</p>
	</header>

	<div
		class="flex flex-col gap-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
	>
		<h2 class="text-sm font-medium text-ink dark:text-ink-dark">New agent</h2>
		{#key createFormKey}
			<AgentForm
				providers={providerList}
				submitLabel="Create agent"
				busyLabel="Creating…"
				busy={creating}
				error={createError}
				onsubmit={handleCreate}
			/>
		{/key}
	</div>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else}
		{#if actionError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{actionError}</p>
		{/if}
		<ul class="flex flex-col gap-3">
			{#each agentList as agent (agent.id)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					{#if editingAgentId === agent.id}
						<AgentForm
							providers={providerList}
							initial={{
								name: agent.name,
								description: agent.description,
								instructions: agent.instructions,
								model: agent.model
							}}
							submitLabel="Save changes"
							busyLabel="Saving…"
							busy={updating}
							error={updateError}
							onsubmit={(values) => handleUpdate(agent.id, values)}
							oncancel={cancelEdit}
						/>
					{:else}
						<div class="flex items-start justify-between">
							<div>
								<p class="font-medium text-ink dark:text-ink-dark">{agent.name}</p>
								<p class="text-sm text-neutral-600 dark:text-neutral-400">{agent.description}</p>
								<p class="mt-1 font-mono text-xs text-neutral-500">{agent.model}</p>
							</div>
							<div class="flex items-center gap-2">
								<span
									class="rounded-sm px-2 py-0.5 text-xs {agent.agentos_agent_id
										? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
										: 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400'}"
								>
									{agent.agentos_agent_id ? 'registered' : 'provider unresolved'}
								</span>
								<button
									onclick={() => startEdit(agent.id)}
									class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									Edit
								</button>
								<button
									onclick={() => handleDelete(agent.id)}
									class="text-xs text-neutral-500 hover:text-agent-magenta-600"
								>
									Delete
								</button>
							</div>
						</div>
					{/if}

					<div class="mt-3 border-t border-ink/10 pt-3 dark:border-white/10">
						<p class="text-xs text-neutral-600 dark:text-neutral-400">
							Routing: <span class="font-mono">{ruleSummary(rulesByAgent[agent.id])}</span>
						</p>
						<div class="mt-2 flex flex-wrap items-center gap-2">
							<button
								onclick={() => setRule(agent.id, 'always', '')}
								class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
							>
								Always respond
							</button>
							<button
								onclick={() => setRule(agent.id, 'mention_only', '')}
								class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
							>
								@mention only
							</button>
							<input
								type="text"
								bind:value={keywordDrafts[agent.id]}
								placeholder="keyword, keyword, ..."
								class="w-40 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							/>
							<button
								onclick={() => setKeywordRule(agent.id)}
								class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
							>
								Set keywords
							</button>
						</div>
						<div class="mt-2 flex flex-wrap items-center gap-2">
							<span class="text-xs text-neutral-500">Preferred peer capability:</span>
							<input
								type="text"
								bind:value={peerPreferenceDrafts[agent.id]}
								placeholder="e.g. gpu (blank = no preference)"
								class="w-56 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							/>
							<button
								onclick={() => savePeerPreference(agent.id)}
								class="rounded-md border border-ink/15 px-2 py-1 text-xs text-ink dark:border-white/15 dark:text-ink-dark"
							>
								Save
							</button>
						</div>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
