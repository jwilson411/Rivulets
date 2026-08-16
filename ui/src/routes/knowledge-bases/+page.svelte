<script lang="ts">
	import { resolve } from '$app/paths';
	import { knowledgeBases, type KnowledgeBase } from '$lib/api/knowledgeBases';
	import { agents as agentsApi, type Agent } from '$lib/api/agents';
	import { teams as teamsApi, type Team } from '$lib/api/teams';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';

	// Knowledge bases (06-screens.md → Knowledge bases): name, who it
	// belongs to, document count. Creation happens in a sheet.

	let kbList = $state<KnowledgeBase[]>([]);
	let agentList = $state<Agent[]>([]);
	let teamList = $state<Team[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);

	let creating = $state(false);
	let newName = $state('');
	let newScopeType = $state<'agent' | 'team'>('team');
	let newSubjectId = $state('');
	let createBusy = $state(false);
	let createError = $state<string | null>(null);

	let deletingKb = $state<KnowledgeBase | null>(null);
	let deleteBusy = $state(false);
	let deleteError = $state<string | null>(null);

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark';

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
		} catch {
			loadError = "Couldn't load knowledge bases.";
		} finally {
			loading = false;
		}
	}

	refresh();

	function subjectName(kb: KnowledgeBase): string {
		if (kb.scope_type === 'agent') {
			return agentList.find((a) => a.id === kb.agent_id)?.name ?? 'an agent';
		}
		return teamList.find((t) => t.id === kb.team_id)?.name ?? 'a team';
	}

	async function handleCreate(event: SubmitEvent) {
		event.preventDefault();
		if (!newName.trim() || !newSubjectId) return;
		createBusy = true;
		createError = null;
		try {
			await knowledgeBases.create({
				name: newName.trim(),
				scope_type: newScopeType,
				agent_id: newScopeType === 'agent' ? newSubjectId : undefined,
				team_id: newScopeType === 'team' ? newSubjectId : undefined
			});
			creating = false;
			newName = '';
			newSubjectId = '';
			await refresh();
		} catch (err) {
			createError = err instanceof Error ? err.message : "Couldn't create the knowledge base.";
		} finally {
			createBusy = false;
		}
	}

	async function handleDelete() {
		if (!deletingKb) return;
		deleteBusy = true;
		deleteError = null;
		try {
			await knowledgeBases.remove(deletingKb.id);
			deletingKb = null;
			await refresh();
		} catch {
			deleteError = "Couldn't delete this knowledge base. Try again.";
		} finally {
			deleteBusy = false;
		}
	}
</script>

<div class="mx-auto max-w-[820px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Bases</h1>
		<Button onclick={() => (creating = true)}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New knowledge base
		</Button>
	</div>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if kbList.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			No knowledge bases yet.
		</p>
	{:else}
		<div class="flex flex-col gap-3">
			{#each kbList as kb (kb.id)}
				<div
					class="flex min-h-16 items-center gap-3.5 rounded-2xl border border-line bg-surface px-6 py-5 hover:border-accent dark:border-line-dark dark:bg-surface-dark dark:hover:border-accent-dark"
				>
					<a href={resolve('/knowledge-bases/[id]', { id: kb.id })} class="min-w-0 flex-1">
						<span class="block text-base font-semibold text-ink dark:text-ink-dark">{kb.name}</span>
						<span class="block text-sm text-muted dark:text-muted-dark">
							Belongs to {subjectName(kb)} · {kb.document_count} document{kb.document_count === 1
								? ''
								: 's'}
						</span>
					</a>
					<button
						type="button"
						onclick={() => (deletingKb = kb)}
						class="flex-none text-sm font-medium text-muted hover:text-danger dark:text-muted-dark"
					>
						Delete
					</button>
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if creating}
	<Sheet title="New knowledge base" onClose={() => (creating = false)} width={480}>
		<form id="new-kb-form" onsubmit={handleCreate} class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="new-kb-name">
					Name
				</label>
				<input
					id="new-kb-name"
					type="text"
					bind:value={newName}
					placeholder="Launch notes"
					class={inputClass}
				/>
			</div>
			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">Belongs to</span>
				<div class="flex gap-2">
					<button
						type="button"
						onclick={() => {
							newScopeType = 'team';
							newSubjectId = '';
						}}
						aria-pressed={newScopeType === 'team'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {newScopeType ===
						'team'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						A team
					</button>
					<button
						type="button"
						onclick={() => {
							newScopeType = 'agent';
							newSubjectId = '';
						}}
						aria-pressed={newScopeType === 'agent'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {newScopeType ===
						'agent'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						One agent
					</button>
				</div>
				<select bind:value={newSubjectId} aria-label="Who it belongs to" class={inputClass}>
					<option value="">
						{newScopeType === 'agent' ? 'Choose an agent…' : 'Choose a team…'}
					</option>
					{#each newScopeType === 'agent' ? agentList : teamList as subject (subject.id)}
						<option value={subject.id}>{subject.name}</option>
					{/each}
				</select>
			</div>
			{#if createError}
				<p class="text-sm text-danger">{createError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (creating = false)}>Cancel</Button>
			<Button
				disabled={createBusy || !newName.trim() || !newSubjectId}
				onclick={() => (document.getElementById('new-kb-form') as HTMLFormElement).requestSubmit()}
			>
				Create knowledge base
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if deletingKb}
	<Sheet title="Delete {deletingKb.name}?" onClose={() => (deletingKb = null)} width={480}>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Its documents are removed and agents stop searching it.
		</p>
		{#if deleteError}
			<p class="text-sm text-danger">{deleteError}</p>
		{/if}
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (deletingKb = null)}>Cancel</Button>
			<Button variant="destructive" onclick={handleDelete} disabled={deleteBusy}>
				{deleteBusy ? 'Deleting…' : 'Delete knowledge base'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
