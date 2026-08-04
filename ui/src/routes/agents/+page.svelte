<script lang="ts">
	import { agents, type Agent, type RoutingRule, type RuleType } from '$lib/api/agents';

	let agentList = $state<Agent[]>([]);
	let rulesByAgent = $state<Record<string, RoutingRule[]>>({});
	let loadError = $state<string | null>(null);

	let name = $state('');
	let description = $state('');
	let instructions = $state('');
	let model = $state('');
	let creating = $state(false);
	let createError = $state<string | null>(null);

	let keywordDrafts = $state<Record<string, string>>({});

	async function refresh() {
		loadError = null;
		try {
			agentList = await agents.list();
			const entries = await Promise.all(
				agentList.map(async (a) => [a.id, await agents.getRoutingRules(a.id)] as const)
			);
			rulesByAgent = Object.fromEntries(entries);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load agents';
		}
	}

	refresh();

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		createError = null;
		if (!name.trim() || !description.trim() || !instructions.trim() || !model.trim()) return;
		creating = true;
		try {
			await agents.create({
				name: name.trim(),
				description: description.trim(),
				instructions: instructions.trim(),
				model: model.trim()
			});
			name = '';
			description = '';
			instructions = '';
			model = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : 'Failed to create agent';
		} finally {
			creating = false;
		}
	}

	async function setRule(agentId: string, ruleType: RuleType, pattern: string) {
		await agents.setRoutingRules(agentId, [{ rule_type: ruleType, pattern, priority: 10 }]);
		rulesByAgent[agentId] = await agents.getRoutingRules(agentId);
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

	function ruleSummary(rules: RoutingRule[] | undefined): string {
		if (!rules || rules.length === 0) return 'No routing rules — only @mention triggers this agent';
		return rules
			.map((r) => (r.rule_type === 'keyword' ? `keyword: ${r.pattern}` : r.rule_type))
			.join(', ');
	}

	async function handleDelete(agentId: string) {
		await agents.remove(agentId);
		await refresh();
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header>
		<h1 class="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Agents</h1>
		<p class="text-sm text-zinc-500">
			Agents you create here register with AgentOS automatically (FR-3.2).
		</p>
	</header>

	<form
		onsubmit={handleCreate}
		class="flex flex-col gap-3 rounded-md border border-zinc-200 p-4 dark:border-zinc-800"
	>
		<h2 class="text-sm font-medium text-zinc-700 dark:text-zinc-300">New agent</h2>
		<input
			type="text"
			bind:value={name}
			placeholder="Name"
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<input
			type="text"
			bind:value={description}
			placeholder="Description (used by the dispatcher for routing)"
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<textarea
			bind:value={instructions}
			placeholder="Instructions (system prompt)"
			rows="3"
			class="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
		></textarea>
		<input
			type="text"
			bind:value={model}
			placeholder="provider:model_name (e.g. anthropic:claude-3-5-haiku-latest)"
			class="rounded-md border border-zinc-300 px-3 py-2 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900"
		/>
		<p class="text-xs text-zinc-500">
			Provider must already be configured under Settings &gt; Providers via the API — supported:
			anthropic, openai, deepseek, openai_compatible.
		</p>
		<button
			type="submit"
			disabled={creating}
			class="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
		>
			{creating ? 'Creating…' : 'Create agent'}
		</button>
		{#if createError}
			<p class="text-sm text-red-600 dark:text-red-400">{createError}</p>
		{/if}
	</form>

	{#if loadError}
		<p class="text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else}
		<ul class="flex flex-col gap-3">
			{#each agentList as agent (agent.id)}
				<li class="rounded-md border border-zinc-200 p-4 dark:border-zinc-800">
					<div class="flex items-start justify-between">
						<div>
							<p class="font-medium text-zinc-900 dark:text-zinc-50">{agent.name}</p>
							<p class="text-sm text-zinc-500">{agent.description}</p>
							<p class="mt-1 font-mono text-xs text-zinc-400">{agent.model}</p>
						</div>
						<div class="flex items-center gap-2">
							<span
								class="rounded-full px-2 py-0.5 text-xs {agent.agentos_agent_id
									? 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400'
									: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400'}"
							>
								{agent.agentos_agent_id ? 'registered' : 'provider unresolved'}
							</span>
							<button
								onclick={() => handleDelete(agent.id)}
								class="text-xs text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
							>
								Delete
							</button>
						</div>
					</div>

					<div class="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-900">
						<p class="text-xs text-zinc-500">
							Routing: <span class="font-mono">{ruleSummary(rulesByAgent[agent.id])}</span>
						</p>
						<div class="mt-2 flex flex-wrap items-center gap-2">
							<button
								onclick={() => setRule(agent.id, 'always', '')}
								class="rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700"
							>
								Always respond
							</button>
							<button
								onclick={() => setRule(agent.id, 'mention_only', '')}
								class="rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700"
							>
								@mention only
							</button>
							<input
								type="text"
								bind:value={keywordDrafts[agent.id]}
								placeholder="keyword, keyword, ..."
								class="w-40 rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
							/>
							<button
								onclick={() => setKeywordRule(agent.id)}
								class="rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700"
							>
								Set keywords
							</button>
						</div>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
