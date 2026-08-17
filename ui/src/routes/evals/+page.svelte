<script lang="ts">
	import { agents, type Agent } from '$lib/api/agents';
	import { workflows, type Workflow } from '$lib/api/workflows';
	import {
		evals,
		type EvalCase,
		type EvalCaseResult,
		type EvalRun,
		type EvalSuite,
		type JudgeType
	} from '$lib/api/evals';
	import { auth } from '$lib/api/auth.svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { timeAgo } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import ErrorBanner from '$lib/ui/ErrorBanner.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import SkeletonCards from '$lib/ui/SkeletonCards.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';

	// Evals (06-screens.md → Evals, mockup 2f): suite cards with a 48px Run
	// button; cases open in a sheet; judge types in plain language — "Exact
	// text", "Contains", "A model grades it", "A specific tool was used".

	const JUDGE_TYPE_LABELS: Record<JudgeType, string> = {
		exact: 'Exact text',
		substring: 'Contains',
		llm_judge: 'A model grades it',
		structural: 'A specific tool was used'
	};

	let suiteList = $state<EvalSuite[]>([]);
	let agentList = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let lastRunBySuite = $state<Record<string, EvalRun | null>>({});
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	// Agents / workflows a guest's suite writes would 403 against.
	let scopedAgentIds = new SvelteSet<string>();
	let scopedWorkflowIds = new SvelteSet<string>();

	// #355: the server 403s an invite-grant suite create/run against an
	// unpublished workflow (owner-only draft runs), so don't offer drafts
	// a guest can't actually fire. Owners still see them, marked as drafts.
	const selectableWorkflows = $derived(
		auth.grant === 'owner' ? workflowList : workflowList.filter((workflow) => workflow.published)
	);
	// #393 (#326 leftover): same idea for a scoped agent -- create/run/delete
	// against that subject 403s, so don't offer it in the picker either.
	const selectableAgents = $derived(
		auth.grant === 'owner' ? agentList : agentList.filter((agent) => !scopedAgentIds.has(agent.id))
	);

	function suiteSubjectGated(suite: EvalSuite): boolean {
		if (auth.grant === 'owner') return false;
		if (suite.agent_id && scopedAgentIds.has(suite.agent_id)) return true;
		if (suite.workflow_id && scopedWorkflowIds.has(suite.workflow_id)) return true;
		return false;
	}

	function suiteRunGated(suite: EvalSuite): boolean {
		if (suiteSubjectGated(suite)) return true;
		if (auth.grant === 'owner' || !suite.workflow_id) return false;
		const workflow = workflowList.find((item) => item.id === suite.workflow_id);
		return workflow !== undefined && !workflow.published;
	}

	async function refresh() {
		loadError = null;
		try {
			const [loadedSuites, loadedAgents, loadedWorkflows] = await Promise.all([
				evals.listSuites(),
				agents.list(),
				workflows.list()
			]);
			suiteList = loadedSuites;
			agentList = loadedAgents;
			workflowList = loadedWorkflows;
			// Latest result per suite drives the card's pass/fail pill.
			const lastRuns = await Promise.all(
				loadedSuites.map(async (suite) => {
					const runList = await evals.listRuns(suite.id).catch(() => [] as EvalRun[]);
					return [suite.id, runList[0] ?? null] as const;
				})
			);
			lastRunBySuite = Object.fromEntries(lastRuns);
			if (auth.grant !== 'owner') {
				const nextScopedAgents = new SvelteSet<string>();
				await Promise.all(
					loadedAgents.map(async (agent) => {
						try {
							const out = await agents.getToolScopes(agent.id);
							if (out.scopes.length > 0) nextScopedAgents.add(agent.id);
						} catch {
							nextScopedAgents.add(agent.id);
						}
					})
				);
				const workflowIds = [
					...new Set(
						loadedSuites.map((suite) => suite.workflow_id).filter((id): id is string => id !== null)
					)
				];
				const nextScopedWorkflows = new SvelteSet<string>();
				await Promise.all(
					workflowIds.map(async (id) => {
						try {
							const nodes = await workflows.listNodes(id);
							if (
								nodes.some((node) => node.agent_id !== null && nextScopedAgents.has(node.agent_id))
							) {
								nextScopedWorkflows.add(id);
							}
						} catch {
							nextScopedWorkflows.add(id);
						}
					})
				);
				scopedAgentIds.clear();
				for (const id of nextScopedAgents) scopedAgentIds.add(id);
				scopedWorkflowIds.clear();
				for (const id of nextScopedWorkflows) scopedWorkflowIds.add(id);
			} else {
				scopedAgentIds.clear();
				scopedWorkflowIds.clear();
			}
		} catch {
			loadError = "Couldn't load evals.";
		} finally {
			loading = false;
		}
	}

	refresh();

	function subjectLabel(suite: EvalSuite): string {
		return suite.subject_type === 'agent' ? suite.subject_name : `/${suite.subject_name}`;
	}

	function lastRunTone(run: EvalRun): 'accent' | 'warn' | 'danger' {
		if (run.pass_count === run.case_count) return 'accent';
		if (run.fail_count > 0 && run.pass_count === 0) return 'danger';
		return 'warn';
	}

	// --- Suite creation ---

	let showCreateSuite = $state(false);
	let suiteNameDraft = $state('');
	let suiteDescriptionDraft = $state('');
	let suiteSubjectType = $state<'agent' | 'workflow'>('agent');
	let suiteSubjectId = $state('');
	let createSuiteBusy = $state(false);
	let createSuiteError = $state<string | null>(null);

	function openCreateSuite() {
		showCreateSuite = true;
		suiteNameDraft = '';
		suiteDescriptionDraft = '';
		suiteSubjectType = 'agent';
		suiteSubjectId = '';
		createSuiteError = null;
	}

	async function handleCreateSuite() {
		if (!suiteNameDraft.trim() || !suiteSubjectId) return;
		createSuiteBusy = true;
		createSuiteError = null;
		try {
			await evals.createSuite({
				name: suiteNameDraft.trim(),
				description: suiteDescriptionDraft.trim() || undefined,
				agent_id: suiteSubjectType === 'agent' ? suiteSubjectId : undefined,
				workflow_id: suiteSubjectType === 'workflow' ? suiteSubjectId : undefined
			});
			showCreateSuite = false;
			await refresh();
		} catch (err) {
			createSuiteError = err instanceof Error ? err.message : "Couldn't create the suite.";
		} finally {
			createSuiteBusy = false;
		}
	}

	async function handleDeleteSuite(suiteId: string) {
		const suite = suiteList.find((item) => item.id === suiteId);
		if (suite && suiteSubjectGated(suite)) return;
		try {
			await evals.deleteSuite(suiteId);
			casesSuite = null;
			await refresh();
		} catch {
			loadError = "Couldn't delete that suite.";
		}
	}

	// --- Cases (open in a sheet) ---

	let casesSuite = $state<EvalSuite | null>(null);
	let caseList = $state<EvalCase[]>([]);
	let casesError = $state<string | null>(null);
	let confirmingSuiteDelete = $state(false);

	let showAddCase = $state(false);
	let caseNameDraft = $state('');
	let caseInputDraft = $state('');
	let caseJudgeTypeDraft = $state<JudgeType>('exact');
	let caseExpectedOutputDraft = $state('');
	let caseRubricDraft = $state('');
	let caseToolNameDraft = $state('');
	let caseToolArgsDraft = $state('');
	let addCaseBusy = $state(false);
	let addCaseError = $state<string | null>(null);

	async function openCases(suite: EvalSuite) {
		casesError = null;
		showAddCase = false;
		confirmingSuiteDelete = false;
		try {
			caseList = await evals.listCases(suite.id);
			casesSuite = suite;
		} catch {
			loadError = "Couldn't load that suite's cases.";
		}
	}

	function openAddCase() {
		showAddCase = true;
		caseNameDraft = '';
		caseInputDraft = '';
		caseJudgeTypeDraft = 'exact';
		caseExpectedOutputDraft = '';
		caseRubricDraft = '';
		caseToolNameDraft = '';
		caseToolArgsDraft = '';
		addCaseError = null;
	}

	async function handleAddCase() {
		if (!casesSuite || !caseNameDraft.trim() || !caseInputDraft.trim()) return;
		let expectedToolArgs: Record<string, unknown> | undefined;
		if (caseJudgeTypeDraft === 'structural' && caseToolArgsDraft.trim()) {
			try {
				expectedToolArgs = JSON.parse(caseToolArgsDraft) as Record<string, unknown>;
			} catch {
				addCaseError = 'Expected tool inputs must be valid JSON, or left blank.';
				return;
			}
		}
		addCaseBusy = true;
		addCaseError = null;
		try {
			await evals.createCase(casesSuite.id, {
				name: caseNameDraft.trim(),
				input_content: caseInputDraft,
				judge_type: caseJudgeTypeDraft,
				expected_output:
					caseJudgeTypeDraft === 'exact' || caseJudgeTypeDraft === 'substring'
						? caseExpectedOutputDraft
						: undefined,
				rubric: caseJudgeTypeDraft === 'llm_judge' ? caseRubricDraft : undefined,
				expected_tool_name: caseJudgeTypeDraft === 'structural' ? caseToolNameDraft : undefined,
				expected_tool_args: expectedToolArgs
			});
			showAddCase = false;
			caseList = await evals.listCases(casesSuite.id);
			await refresh();
		} catch (err) {
			addCaseError = err instanceof Error ? err.message : "Couldn't add that case.";
		} finally {
			addCaseBusy = false;
		}
	}

	async function handleDeleteCase(caseId: string) {
		if (!casesSuite) return;
		try {
			await evals.deleteCase(casesSuite.id, caseId);
			caseList = await evals.listCases(casesSuite.id);
			await refresh();
		} catch {
			casesError = "Couldn't remove that case.";
		}
	}

	// --- Running suites + run history ---

	let runBusy = $state<Record<string, boolean>>({});
	let runError = $state<Record<string, string | null>>({});
	let expandedRunsSuiteId = $state<string | null>(null);
	let runsBySuite = $state<Record<string, EvalRun[]>>({});
	let casesForResults = $state<Record<string, EvalCase[]>>({});
	let runsError = $state<string | null>(null);
	let expandedRunId = $state<string | null>(null);
	let resultsByRun = $state<Record<string, EvalCaseResult[]>>({});
	let resultsError = $state<string | null>(null);

	async function handleRunSuite(suite: EvalSuite) {
		if (suiteRunGated(suite)) return;
		runBusy[suite.id] = true;
		runError[suite.id] = null;
		try {
			await evals.run(suite.id);
			expandedRunsSuiteId = suite.id;
			runsBySuite[suite.id] = await evals.listRuns(suite.id);
			lastRunBySuite[suite.id] = runsBySuite[suite.id][0] ?? null;
		} catch {
			runError[suite.id] = "Couldn't run that suite. Try again.";
		} finally {
			runBusy[suite.id] = false;
		}
	}

	async function toggleRuns(suiteId: string) {
		if (expandedRunsSuiteId === suiteId) {
			expandedRunsSuiteId = null;
			return;
		}
		expandedRunsSuiteId = suiteId;
		runsError = null;
		try {
			runsBySuite[suiteId] = await evals.listRuns(suiteId);
			casesForResults[suiteId] = await evals.listCases(suiteId).catch(() => [] as EvalCase[]);
		} catch {
			runsError = "Couldn't load run history.";
		}
	}

	async function toggleRunResults(suiteId: string, runId: string) {
		if (expandedRunId === runId) {
			expandedRunId = null;
			return;
		}
		expandedRunId = runId;
		if (resultsByRun[runId]) return;
		resultsError = null;
		try {
			resultsByRun[runId] = await evals.listResults(suiteId, runId);
		} catch {
			resultsError = "Couldn't load results.";
		}
	}

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';
	const areaClass =
		'rounded-lg border border-line bg-surface px-4 py-3 text-base text-ink placeholder:text-muted focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:placeholder:text-muted-dark dark:focus:border-accent-dark';
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 pb-24 md:px-10 md:pb-12">
	<div class="mb-6 flex items-center justify-between gap-4">
		<h1 class="font-display text-[28px] font-semibold text-ink dark:text-ink-dark">Evals</h1>
		<Button onclick={openCreateSuite}>
			<Icon name="plus" class="h-[18px] w-[18px]" />
			New suite
		</Button>
	</div>

	{#if loading}
		<SkeletonCards count={2} />
	{:else if loadError}
		<ErrorBanner message={loadError} onRetry={refresh} />
	{:else if suiteList.length === 0}
		<p class="py-8 text-center text-base text-muted dark:text-muted-dark">
			No eval suites yet. A suite is a fixed set of inputs judged against expected outcomes, so a
			behavior change shows up as a failing case.
		</p>
	{:else}
		<div class="flex flex-col gap-4">
			{#each suiteList as suite (suite.id)}
				{@const lastRun = lastRunBySuite[suite.id]}
				<div
					class="rounded-2xl border border-line bg-surface px-7 py-6 dark:border-line-dark dark:bg-surface-dark"
				>
					<div class="mb-1.5 flex flex-wrap items-center gap-3">
						<span class="text-lg font-semibold text-ink dark:text-ink-dark">{suite.name}</span>
						{#if lastRun}
							<StatusPill tone={lastRunTone(lastRun)}>
								{lastRun.pass_count}/{lastRun.case_count} passed
							</StatusPill>
						{/if}
					</div>
					<p class="mb-5 text-[15px] text-muted dark:text-muted-dark">
						Targets {suite.subject_type === 'agent' ? 'agent' : 'workflow'}
						<span
							class="rounded-md bg-paper px-1.5 py-0.5 font-mono text-[13px] dark:bg-paper-dark"
						>
							{subjectLabel(suite)}
						</span>
						· {suite.case_count} case{suite.case_count === 1 ? '' : 's'}
					</p>

					<div class="flex flex-wrap items-center gap-4">
						{#if !suiteRunGated(suite)}
							<Button
								class="px-8"
								disabled={runBusy[suite.id] || suite.case_count === 0}
								onclick={() => handleRunSuite(suite)}
							>
								{runBusy[suite.id] ? 'Running…' : 'Run'}
							</Button>
						{/if}
						<button
							type="button"
							onclick={() => openCases(suite)}
							class="text-[15px] font-semibold text-accent hover:underline dark:text-accent-dark"
						>
							Cases
						</button>
						<button
							type="button"
							onclick={() => toggleRuns(suite.id)}
							class="text-[15px] font-semibold text-accent hover:underline dark:text-accent-dark"
						>
							{expandedRunsSuiteId === suite.id ? 'Hide history' : 'History'}
						</button>
					</div>
					{#if runError[suite.id]}
						<p class="mt-3 text-sm text-danger">{runError[suite.id]}</p>
					{/if}

					{#if expandedRunsSuiteId === suite.id}
						<div class="mt-5 border-t border-line pt-4 dark:border-line-dark">
							{#if runsError}
								<p class="text-sm text-danger">{runsError}</p>
							{:else if !runsBySuite[suite.id]}
								<div class="breath h-3 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
							{:else if runsBySuite[suite.id].length === 0}
								<p class="text-sm text-muted dark:text-muted-dark">No runs yet.</p>
							{:else}
								<div class="flex flex-col gap-2">
									{#each runsBySuite[suite.id] as run (run.id)}
										<div class="rounded-xl border border-line dark:border-line-dark">
											<button
												type="button"
												onclick={() => toggleRunResults(suite.id, run.id)}
												class="flex h-12 w-full items-center gap-3 px-4 text-left"
											>
												<Icon
													name="chevron-right"
													class="h-3.5 w-3.5 flex-none text-muted transition-transform duration-150 dark:text-muted-dark {expandedRunId ===
													run.id
														? 'rotate-90'
														: ''}"
												/>
												<span class="text-sm text-ink dark:text-ink-dark">
													{timeAgo(run.started_at)}
												</span>
												<StatusPill tone={lastRunTone(run)} class="ml-auto h-[22px] text-xs">
													{run.pass_count}/{run.case_count} passed
												</StatusPill>
											</button>
											{#if expandedRunId === run.id}
												<div class="border-t border-line px-4 py-3 dark:border-line-dark">
													{#if resultsError}
														<p class="text-sm text-danger">{resultsError}</p>
													{:else if !resultsByRun[run.id]}
														<div
															class="breath h-3 w-1/2 rounded-full bg-line dark:bg-line-dark"
														></div>
													{:else}
														<div class="flex flex-col gap-3">
															{#each resultsByRun[run.id] as result (result.id)}
																{@const caseName =
																	casesForResults[suite.id]?.find((c) => c.id === result.case_id)
																		?.name ?? 'Deleted case'}
																<div class="flex flex-col gap-1 text-sm">
																	<div class="flex items-center gap-2.5">
																		<span
																			class="h-2 w-2 rounded-full {result.status === 'passed'
																				? 'bg-accent dark:bg-accent-dark'
																				: result.status === 'failed'
																					? 'bg-danger'
																					: 'bg-warn'}"
																		></span>
																		<span class="font-medium text-ink dark:text-ink-dark">
																			{caseName}
																		</span>
																		{#if result.score !== null}
																			<span class="text-[13px] text-muted dark:text-muted-dark">
																				score {result.score.toFixed(2)}
																			</span>
																		{/if}
																	</div>
																	{#if result.actual_output}
																		<p class="pl-4.5 text-muted dark:text-muted-dark">
																			{result.actual_output}
																		</p>
																	{/if}
																	{#if result.judge_reasoning}
																		<p
																			class="pl-4.5 text-[13px] text-muted italic dark:text-muted-dark"
																		>
																			{result.judge_reasoning}
																		</p>
																	{/if}
																	{#if result.error_message}
																		<p class="pl-4.5 text-[13px] text-danger">
																			{result.error_message}
																		</p>
																	{/if}
																</div>
															{/each}
														</div>
													{/if}
												</div>
											{/if}
										</div>
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if showCreateSuite}
	<Sheet title="New suite" onClose={() => (showCreateSuite = false)} width={480}>
		<div class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="suite-name">
					Name
				</label>
				<input
					id="suite-name"
					type="text"
					bind:value={suiteNameDraft}
					placeholder="retry-coverage"
					class={inputClass}
				/>
			</div>
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="suite-desc">
					What it checks
				</label>
				<input
					id="suite-desc"
					type="text"
					bind:value={suiteDescriptionDraft}
					placeholder="Optional"
					class={inputClass}
				/>
			</div>
			<div class="flex flex-col gap-2">
				<span class="text-sm font-semibold text-ink dark:text-ink-dark">Targets</span>
				<div class="flex gap-2">
					<button
						type="button"
						onclick={() => {
							suiteSubjectType = 'agent';
							suiteSubjectId = '';
						}}
						aria-pressed={suiteSubjectType === 'agent'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {suiteSubjectType ===
						'agent'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						An agent
					</button>
					<button
						type="button"
						onclick={() => {
							suiteSubjectType = 'workflow';
							suiteSubjectId = '';
						}}
						aria-pressed={suiteSubjectType === 'workflow'}
						class="flex h-12 flex-1 items-center justify-center rounded-lg text-[15px] {suiteSubjectType ===
						'workflow'
							? 'border-2 border-accent bg-accent-soft font-semibold text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'
							: 'border border-line font-medium text-muted dark:border-line-dark dark:text-muted-dark'}"
					>
						A workflow
					</button>
				</div>
				<select bind:value={suiteSubjectId} aria-label="Target" class={inputClass}>
					<option value="" disabled>
						{suiteSubjectType === 'agent' ? 'Choose an agent…' : 'Choose a workflow…'}
					</option>
					{#if suiteSubjectType === 'agent'}
						{#each selectableAgents as agent (agent.id)}
							<option value={agent.id}>{agent.name}</option>
						{/each}
					{:else}
						{#each selectableWorkflows as workflow (workflow.id)}
							<option value={workflow.id}>
								/{workflow.name}{workflow.published ? '' : ' (draft)'}
							</option>
						{/each}
					{/if}
				</select>
			</div>
			{#if createSuiteError}
				<p class="text-sm text-danger">{createSuiteError}</p>
			{/if}
		</div>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (showCreateSuite = false)}>Cancel</Button>
			<Button
				disabled={createSuiteBusy || !suiteNameDraft.trim() || !suiteSubjectId}
				onclick={handleCreateSuite}
			>
				{createSuiteBusy ? 'Creating…' : 'Create suite'}
			</Button>
		{/snippet}
	</Sheet>
{/if}

{#if casesSuite && !confirmingSuiteDelete}
	<Sheet title="{casesSuite.name} — cases" onClose={() => (casesSuite = null)}>
		{#if casesError}
			<p class="text-sm text-danger">{casesError}</p>
		{/if}
		{#if caseList.length === 0 && !showAddCase}
			<p class="text-[15px] text-muted dark:text-muted-dark">No cases yet.</p>
		{:else}
			<div class="flex flex-col gap-2">
				{#each caseList as evalCase (evalCase.id)}
					<div
						class="flex min-h-12 items-center gap-3 rounded-lg border border-line px-4 dark:border-line-dark"
					>
						<span class="text-[15px] text-ink dark:text-ink-dark">{evalCase.name}</span>
						<span class="ml-auto text-[13px] text-muted dark:text-muted-dark">
							{JUDGE_TYPE_LABELS[evalCase.judge_type]}
						</span>
						<button
							type="button"
							onclick={() => handleDeleteCase(evalCase.id)}
							class="text-sm font-medium text-danger hover:underline"
						>
							Remove
						</button>
					</div>
				{/each}
			</div>
		{/if}

		{#if showAddCase}
			<div class="flex flex-col gap-4 rounded-xl border border-line p-4 dark:border-line-dark">
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-name">
						Case name
					</label>
					<input id="case-name" type="text" bind:value={caseNameDraft} class={inputClass} />
				</div>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-input">
						Input
					</label>
					<textarea id="case-input" rows="2" bind:value={caseInputDraft} class={areaClass}
					></textarea>
				</div>
				<div class="flex flex-col gap-2">
					<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-judge">
						How it's judged
					</label>
					<select id="case-judge" bind:value={caseJudgeTypeDraft} class={inputClass}>
						<option value="exact">Exact text</option>
						<option value="substring">Contains</option>
						<option value="llm_judge">A model grades it</option>
						{#if casesSuite.subject_type === 'agent'}
							<option value="structural">A specific tool was used</option>
						{/if}
					</select>
				</div>
				{#if caseJudgeTypeDraft === 'exact' || caseJudgeTypeDraft === 'substring'}
					<div class="flex flex-col gap-2">
						<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-expected">
							Expected reply
						</label>
						<textarea
							id="case-expected"
							rows="2"
							bind:value={caseExpectedOutputDraft}
							class={areaClass}></textarea>
					</div>
				{:else if caseJudgeTypeDraft === 'llm_judge'}
					<div class="flex flex-col gap-2">
						<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-rubric">
							What a good reply looks like
						</label>
						<textarea
							id="case-rubric"
							rows="2"
							bind:value={caseRubricDraft}
							placeholder="The reply should acknowledge the request and offer a next step."
							class={areaClass}></textarea>
					</div>
				{:else if caseJudgeTypeDraft === 'structural'}
					<div class="flex flex-col gap-2">
						<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-tool">
							Tool that must be used
						</label>
						<input
							id="case-tool"
							type="text"
							bind:value={caseToolNameDraft}
							placeholder="web_search"
							class="{inputClass} font-mono text-sm"
						/>
					</div>
					<div class="flex flex-col gap-2">
						<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="case-tool-args">
							Expected inputs (JSON, optional)
						</label>
						<input
							id="case-tool-args"
							type="text"
							bind:value={caseToolArgsDraft}
							placeholder={'{"query": "cats"}'}
							class="{inputClass} font-mono text-sm"
						/>
					</div>
				{/if}
				{#if addCaseError}
					<p class="text-sm text-danger">{addCaseError}</p>
				{/if}
				<div class="flex justify-end gap-3">
					<Button variant="secondary" size="md" onclick={() => (showAddCase = false)}>Cancel</Button
					>
					<Button
						size="md"
						disabled={addCaseBusy || !caseNameDraft.trim() || !caseInputDraft.trim()}
						onclick={handleAddCase}
					>
						{addCaseBusy ? 'Adding…' : 'Add case'}
					</Button>
				</div>
			</div>
		{:else}
			<Button variant="secondary" size="md" class="self-start" onclick={openAddCase}>
				<Icon name="plus" class="h-4 w-4" />
				Add case
			</Button>
		{/if}

		{#snippet footer()}
			{#if !suiteSubjectGated(casesSuite!)}
				<button
					type="button"
					onclick={() => (confirmingSuiteDelete = true)}
					class="mr-auto text-[15px] font-medium text-danger hover:underline"
				>
					Delete suite
				</button>
			{/if}
			<Button variant="secondary" onclick={() => (casesSuite = null)}>Close</Button>
		{/snippet}
	</Sheet>
{/if}

{#if casesSuite && confirmingSuiteDelete}
	<Sheet
		title="Delete {casesSuite.name}?"
		onClose={() => (confirmingSuiteDelete = false)}
		width={480}
	>
		<p class="text-base leading-normal text-ink dark:text-ink-dark">
			Its cases and run history go with it.
		</p>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (confirmingSuiteDelete = false)}>Cancel</Button>
			<Button variant="destructive" onclick={() => handleDeleteSuite(casesSuite!.id)}>
				Delete suite
			</Button>
		{/snippet}
	</Sheet>
{/if}
