<script lang="ts">
	import {
		agents,
		type Agent,
		type AgentVersion,
		type RoutingRule,
		type RuleType
	} from '$lib/api/agents';
	import { providers as providersApi, type Provider } from '$lib/api/providers';
	import { teams as teamsApi, type TeamDetail } from '$lib/api/teams';
	import AgentForm, { type AgentFormValues } from '$lib/components/AgentForm.svelte';
	import FilterableList, { type ListFilter } from '$lib/components/FilterableList.svelte';

	let agentList = $state<Agent[]>([]);
	let providerList = $state<Provider[]>([]);
	let teamList = $state<TeamDetail[]>([]);
	let rulesByAgent = $state<Record<string, RoutingRule[]>>({});
	let loadError = $state<string | null>(null);

	// Team detail (not just the summary teams.list() returns) is the only
	// way to know which agents are on a team, needed for the "on a
	// team" / "not on a team" filter below.
	const teamAgentIds = $derived(new Set(teamList.flatMap((t) => t.agent_ids)));

	const agentFilters = $derived<ListFilter<Agent>[]>([
		{
			id: 'team',
			label: 'Team',
			options: [
				{ value: 'yes', label: 'On a team' },
				{ value: 'no', label: 'Not on a team' }
			],
			predicate: (agent, value) =>
				value === 'yes' ? teamAgentIds.has(agent.id) : !teamAgentIds.has(agent.id)
		}
	]);

	let creating = $state(false);
	let createError = $state<string | null>(null);
	let createFormKey = $state(0);

	let editingAgentId = $state<string | null>(null);
	let updating = $state(false);
	let updateError = $state<string | null>(null);

	let keywordDrafts = $state<Record<string, string>>({});
	let actionError = $state<string | null>(null);

	let peerPreferenceDrafts = $state<Record<string, string>>({});

	// #104: loaded lazily (only once "Version history" is expanded for a
	// given agent) rather than fetched for every agent on every refresh
	// like routing rules/peer preference above -- version lists aren't
	// needed for the collapsed card view, so there's no reason to pay for
	// them up front.
	let versionsByAgent = $state<Record<string, AgentVersion[]>>({});
	let rollingBackVersion = $state<number | null>(null);
	let versionError = $state<string | null>(null);

	// Draft rule-type selection per agent, driving the exclusive radio group
	// below. Kept separate from `rulesByAgent` so picking a different type
	// doesn't apply anything until the user confirms via applyRuleType.
	let ruleTypeDrafts = $state<Record<string, RuleType>>({});

	const RULE_TYPE_LABELS: Partial<Record<RuleType, string>> = {
		always: 'Always respond',
		mention_only: '@mention only',
		keyword: 'Keyword match'
	};

	function activeRuleType(agentId: string): RuleType {
		return rulesByAgent[agentId]?.[0]?.rule_type ?? 'mention_only';
	}

	function ruleTypeLabel(type: RuleType): string {
		return RULE_TYPE_LABELS[type] ?? type;
	}

	async function refresh() {
		loadError = null;
		try {
			const [loadedAgents, loadedProviders, teamSummaries] = await Promise.all([
				agents.list(),
				providersApi.list(),
				teamsApi.list()
			]);
			agentList = loadedAgents;
			providerList = loadedProviders;
			teamList = await Promise.all(teamSummaries.map((t) => teamsApi.get(t.id)));
			const entries = await Promise.all(
				agentList.map(async (a) => [a.id, await agents.getRoutingRules(a.id)] as const)
			);
			rulesByAgent = Object.fromEntries(entries);
			ruleTypeDrafts = Object.fromEntries(
				entries.map(([id, rules]) => [id, rules[0]?.rule_type ?? 'mention_only'])
			);
			for (const [id, rules] of entries) {
				if (rules[0]?.rule_type === 'keyword' && keywordDrafts[id] === undefined) {
					try {
						keywordDrafts[id] = (JSON.parse(rules[0].pattern) as string[]).join(', ');
					} catch {
						keywordDrafts[id] = rules[0].pattern;
					}
				}
			}
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
	}

	async function applyRuleType(agentId: string) {
		const type = ruleTypeDrafts[agentId] ?? activeRuleType(agentId);
		if (type === 'keyword') {
			await setKeywordRule(agentId);
		} else {
			await setRule(agentId, type, '');
		}
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

	// #100: toggled directly (no separate draft/save step) since it's a
	// single boolean, unlike peer preference's free-text field above.
	async function toggleUnattendedApproval(agent: Agent) {
		actionError = null;
		const next = !agent.approved_for_unattended_tools;
		try {
			await agents.update(agent.id, { approved_for_unattended_tools: next });
			agent.approved_for_unattended_tools = next;
		} catch (err) {
			actionError =
				err instanceof Error ? err.message : 'Failed to update unattended tool approval';
		}
	}

	function ruleSummary(rules: RoutingRule[] | undefined): string {
		if (!rules || rules.length === 0) return 'No routing rules — only @mention triggers this agent';
		return rules
			.map((r) => (r.rule_type === 'keyword' ? `keyword: ${r.pattern}` : r.rule_type))
			.join(', ');
	}

	async function loadVersions(agentId: string) {
		if (versionsByAgent[agentId]) return; // already loaded for this expand
		versionError = null;
		try {
			versionsByAgent[agentId] = await agents.getVersions(agentId);
		} catch (err) {
			versionError = err instanceof Error ? err.message : 'Failed to load version history';
		}
	}

	async function rollback(agent: Agent, version: number) {
		versionError = null;
		rollingBackVersion = version;
		try {
			const updated = await agents.rollbackVersion(agent.id, version);
			agent.instructions = updated.instructions;
			agent.model = updated.model;
			delete versionsByAgent[agent.id]; // stale after rollback -- reload on next expand
			versionsByAgent[agent.id] = await agents.getVersions(agent.id);
			rulesByAgent[agent.id] = await agents.getRoutingRules(agent.id);
		} catch (err) {
			versionError = err instanceof Error ? err.message : 'Failed to roll back agent version';
		} finally {
			rollingBackVersion = null;
		}
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
		<FilterableList
			items={agentList}
			getKey={(agent) => agent.id}
			searchPlaceholder="Search agents…"
			searchPredicate={(agent, q) => agent.name.toLowerCase().includes(q.toLowerCase())}
			filters={agentFilters}
			emptyMessage="No agents yet — create one above."
			noMatchMessage="No agents match your search or filter."
		>
			{#snippet item(agent)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					{#if editingAgentId === agent.id}
						<AgentForm
							providers={providerList}
							initial={{
								name: agent.name,
								description: agent.description,
								instructions: agent.instructions,
								model: agent.model,
								fallback_models: agent.fallback_models
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
								{#if agent.fallback_models.length > 0}
									<p class="font-mono text-xs text-neutral-400">
										fallback: {agent.fallback_models.join(' → ')}
									</p>
								{/if}
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
						<div class="mt-2">
							<p
								id="rule-type-label-{agent.id}"
								class="text-xs font-medium text-neutral-600 dark:text-neutral-400"
							>
								Routing rule (only one can be active at a time)
							</p>
							<div
								role="radiogroup"
								aria-labelledby="rule-type-label-{agent.id}"
								class="mt-1 flex flex-wrap items-center gap-3"
							>
								<label class="flex items-center gap-1 text-xs text-ink dark:text-ink-dark">
									<input
										type="radio"
										name="rule-type-{agent.id}"
										value="always"
										bind:group={ruleTypeDrafts[agent.id]}
									/>
									Always respond
								</label>
								<label class="flex items-center gap-1 text-xs text-ink dark:text-ink-dark">
									<input
										type="radio"
										name="rule-type-{agent.id}"
										value="mention_only"
										bind:group={ruleTypeDrafts[agent.id]}
									/>
									@mention only
								</label>
								<label class="flex items-center gap-1 text-xs text-ink dark:text-ink-dark">
									<input
										type="radio"
										name="rule-type-{agent.id}"
										value="keyword"
										bind:group={ruleTypeDrafts[agent.id]}
									/>
									Keyword match
								</label>
								{#if ruleTypeDrafts[agent.id] === 'keyword'}
									<input
										type="text"
										bind:value={keywordDrafts[agent.id]}
										placeholder="keyword, keyword, ..."
										class="w-40 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-xs text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
									/>
								{/if}
							</div>
							{#if ruleTypeDrafts[agent.id] && ruleTypeDrafts[agent.id] !== activeRuleType(agent.id)}
								<p
									class="mt-1 text-xs text-agent-magenta-700 dark:text-agent-magenta-400"
									role="alert"
								>
									Switching to "{ruleTypeLabel(ruleTypeDrafts[agent.id])}" will replace the current
									rule ("{ruleTypeLabel(activeRuleType(agent.id))}"). Nothing changes until you
									apply.
								</p>
							{/if}
							<button
								onclick={() => applyRuleType(agent.id)}
								disabled={ruleTypeDrafts[agent.id] === 'keyword' &&
									!keywordDrafts[agent.id]?.trim()}
								class="mt-2 rounded-md border border-ink/15 px-2 py-1 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/15 dark:text-ink-dark"
							>
								Apply routing rule
							</button>
						</div>
						<details class="mt-3">
							<summary
								class="cursor-pointer text-xs font-medium text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
							>
								Advanced: peer capability preference
							</summary>
							<div class="mt-2 flex flex-col gap-2">
								<p class="text-xs text-neutral-500">
									Only matters when this agent syncs across multiple machines (P2P) — it prefers to
									run on a peer advertising this capability tag.
								</p>
								<div class="flex flex-wrap items-center gap-2">
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
						</details>
						<details class="mt-2">
							<summary
								class="cursor-pointer text-xs font-medium text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
							>
								Advanced: unattended sensitive tool use
							</summary>
							<div class="mt-2 flex flex-col gap-2">
								<p class="text-xs text-neutral-500">
									If this agent has a sensitive tool assigned (runs code, makes outbound HTTP calls,
									writes files, or queries the DB), it's blocked from using that tool when invoked
									unattended — a schedule fire or an auto-remediation run, where nobody is watching
									the tool call happen live. Ordinary chat/slash-command use is never affected by
									this.
								</p>
								<label class="flex items-center gap-2 text-xs text-ink dark:text-ink-dark">
									<input
										type="checkbox"
										checked={agent.approved_for_unattended_tools}
										onchange={() => toggleUnattendedApproval(agent)}
									/>
									Approve this agent's sensitive tools for unattended use
								</label>
							</div>
						</details>
						<details class="mt-2" ontoggle={() => loadVersions(agent.id)}>
							<summary
								class="cursor-pointer text-xs font-medium text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
							>
								Version history
							</summary>
							<div class="mt-2 flex flex-col gap-2">
								<p class="text-xs text-neutral-500">
									Every change to instructions or model is recorded here. Rolling back restores that
									version's instructions/model onto this agent — the rollback itself becomes the
									newest entry, so nothing is lost.
								</p>
								{#if versionError}
									<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
										{versionError}
									</p>
								{/if}
								{#if versionsByAgent[agent.id]}
									<ul class="flex flex-col gap-2">
										{#each versionsByAgent[agent.id] as v (v.version)}
											<li class="rounded-md border border-ink/10 p-2 text-xs dark:border-white/10">
												<div class="flex items-center justify-between gap-2">
													<span class="font-medium text-ink dark:text-ink-dark">
														Version {v.version}
														{#if v.version === versionsByAgent[agent.id][0].version}
															<span class="text-neutral-500">(current)</span>
														{/if}
													</span>
													<span class="text-neutral-500">{v.created_at}</span>
												</div>
												<p class="mt-1 font-mono text-neutral-500">{v.model}</p>
												<p class="mt-1 whitespace-pre-wrap text-neutral-600 dark:text-neutral-400">
													{v.instructions}
												</p>
												{#if v.version !== versionsByAgent[agent.id][0].version}
													<button
														onclick={() => rollback(agent, v.version)}
														disabled={rollingBackVersion !== null}
														class="mt-2 rounded-md border border-ink/15 px-2 py-1 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/15 dark:text-ink-dark"
													>
														{rollingBackVersion === v.version
															? 'Rolling back…'
															: 'Roll back to this version'}
													</button>
												{/if}
											</li>
										{/each}
									</ul>
								{/if}
							</div>
						</details>
					</div>
				</li>
			{/snippet}
		</FilterableList>
	{/if}
</div>
