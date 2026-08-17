<script lang="ts">
	import { agents, type Agent, type AgentVersion, type RoutingRule } from '$lib/api/agents';
	import { providers as providersApi, type Provider } from '$lib/api/providers';
	import { teams as teamsApi, type TeamDetail } from '$lib/api/teams';
	import { tools as toolsApi, type Tool } from '$lib/api/tools';
	import { agentInk, INK_AVATAR } from '$lib/ink';
	import AgentSheet from '$lib/components/AgentSheet.svelte';
	import Button from '$lib/ui/Button.svelte';
	import Disc from '$lib/ui/Disc.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Agents (06-screens.md → Agents, mockup 1i): cards, not a form. All
	// configuration lives in the Agent sheet — the list page never shows
	// routing radios or advanced fields.

	let agentList = $state<Agent[]>([]);
	let providerList = $state<Provider[]>([]);
	let teamList = $state<TeamDetail[]>([]);
	let toolList = $state<Tool[]>([]);
	let scopeCatalog = $state<string[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let search = $state('');

	// Sheet state: null = closed, 'new' = create, otherwise the agent id
	// being edited (with its per-agent extras fetched just-in-time).
	let sheetOpen = $state<'new' | string | null>(null);
	let sheetAgent = $state<Agent | null>(null);
	let sheetRules = $state<RoutingRule[]>([]);
	let sheetToolIds = $state<string[]>([]);
	let sheetScopes = $state<string[]>([]);
	let sheetPeerTag = $state('');
	let sheetVersions = $state<AgentVersion[]>([]);
	let sheetKey = $state(0);

	let visible = $derived(
		agentList.filter((a) => a.name.toLowerCase().includes(search.trim().toLowerCase()))
	);

	function teamsFor(agentId: string): TeamDetail[] {
		return teamList.filter((t) => t.agent_ids.includes(agentId));
	}

	async function refresh() {
		loadError = null;
		try {
			// providersApi.list() is OwnerGrant-only (server-side) -- an invite
			// grant gets a 403 here even though agent CRUD itself isn't
			// owner-gated. Treat that as "no provider catalog" rather than
			// letting it fail the whole Promise.all and blank the agent list.
			const [loadedAgents, loadedProviders, teamSummaries, loadedTools, loadedScopes] =
				await Promise.all([
					agents.list(),
					providersApi.list().catch(() => []),
					teamsApi.list(),
					toolsApi.list(),
					toolsApi.listScopes().catch(() => [])
				]);
			agentList = loadedAgents;
			providerList = loadedProviders;
			toolList = loadedTools;
			scopeCatalog = loadedScopes;
			teamList = await Promise.all(teamSummaries.map((t) => teamsApi.get(t.id)));
		} catch {
			loadError = "Couldn't load agents.";
		} finally {
			loading = false;
		}
	}

	refresh();

	function openCreate() {
		sheetAgent = null;
		sheetRules = [];
		sheetToolIds = [];
		sheetScopes = [];
		sheetPeerTag = '';
		sheetVersions = [];
		sheetKey += 1;
		sheetOpen = 'new';
	}

	// Per-agent extras (rules, tools, scopes, versions, peer preference)
	// aren't on the list payload — fetched just-in-time when a card opens.
	async function openEdit(agent: Agent) {
		try {
			const [rules, toolIds, scopes, preference, versions] = await Promise.all([
				agents.getRoutingRules(agent.id).catch(() => [] as RoutingRule[]),
				agents
					.getToolIds(agent.id)
					.then((r) => r.tool_ids)
					.catch(() => [] as string[]),
				agents
					.getToolScopes(agent.id)
					.then((r) => r.scopes)
					.catch(() => [] as string[]),
				agents.getPeerPreference(agent.id).catch(() => ({ capability_tag: null })),
				agents.listVersions(agent.id).catch(() => [] as AgentVersion[])
			]);
			sheetAgent = agent;
			sheetRules = rules;
			sheetToolIds = toolIds;
			sheetScopes = scopes;
			sheetPeerTag = preference.capability_tag ?? '';
			sheetVersions = versions;
			sheetKey += 1;
			sheetOpen = agent.id;
		} catch {
			loadError = "Couldn't open that agent. Try again.";
		}
	}

	async function handleSaved() {
		sheetOpen = null;
		await refresh();
	}
</script>

<div class="mx-auto max-w-[900px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Agents</h1>
		<Button onclick={openCreate}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New agent
		</Button>
	</div>

	<label
		class="mb-5 flex h-12 items-center gap-2.5 rounded-lg border border-line bg-surface px-4 focus-within:border-accent dark:border-line-dark dark:bg-surface-dark dark:focus-within:border-accent-dark"
	>
		<span class="sr-only">Search agents</span>
		<Icon name="search" class="h-[18px] w-[18px] flex-none text-muted dark:text-muted-dark" />
		<input
			type="search"
			bind:value={search}
			placeholder="Search agents"
			class="min-w-0 flex-1 appearance-none bg-transparent text-base text-ink placeholder:text-muted focus:outline-none dark:text-ink-dark dark:placeholder:text-muted-dark"
		/>
	</label>

	{#if loading}
		<SkeletonCards count={4} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if visible.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			{search.trim() ? 'No agents match your search.' : 'No agents yet.'}
		</p>
	{:else}
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			{#each visible as agent, i (agent.id)}
				<button
					type="button"
					onclick={() => openEdit(agent)}
					class="flex gap-3.5 rounded-2xl border border-line bg-surface px-5 py-5 text-left hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<Disc name={agent.name} colorClass={INK_AVATAR[agentInk(i)]} size={40} />
					<span class="min-w-0">
						<span class="mb-0.5 flex items-center gap-2">
							<span class="truncate text-base font-semibold text-ink dark:text-ink-dark">
								{agent.name}
							</span>
							<span
								class="flex-none rounded-md bg-paper px-1.5 py-0.5 font-mono text-xs text-muted dark:bg-paper-dark dark:text-muted-dark"
							>
								{agent.model}
							</span>
							{#if !agent.agentos_agent_id}
								<span
									class="flex-none rounded-full bg-warn-soft px-2 py-0.5 text-xs font-semibold text-warn dark:bg-warn-soft-dark dark:text-warn-ink-dark"
									title="This agent has no working model provider yet, so it stays silent."
								>
									Needs a provider
								</span>
							{/if}
						</span>
						<span class="mb-2 block text-[15px] leading-snug text-muted dark:text-muted-dark">
							{agent.description}
						</span>
						{#each teamsFor(agent.id) as team (team.id)}
							<span
								class="mr-1.5 inline-flex h-6 items-center rounded-full bg-accent-soft px-2.5 text-[13px] font-semibold text-accent dark:bg-accent-soft-dark dark:text-accent-dark"
							>
								{team.name}
							</span>
						{/each}
					</span>
				</button>
			{/each}
		</div>
	{/if}
</div>

{#if sheetOpen !== null}
	{#key sheetKey}
		<AgentSheet
			agent={sheetAgent}
			providers={providerList}
			tools={toolList}
			teams={teamList}
			{scopeCatalog}
			initialTeamIds={sheetAgent ? teamsFor(sheetAgent.id).map((team) => team.id) : []}
			initialRules={sheetRules}
			initialToolIds={sheetToolIds}
			initialScopes={sheetScopes}
			initialPeerTag={sheetPeerTag}
			versions={sheetVersions}
			onClose={() => (sheetOpen = null)}
			onSaved={handleSaved}
		/>
	{/key}
{/if}
