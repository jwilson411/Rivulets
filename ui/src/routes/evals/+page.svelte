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
	import { timeAgo } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';

	const JUDGE_TYPE_LABELS: Record<JudgeType, string> = {
		exact: 'Exact match',
		substring: 'Contains text',
		llm_judge: 'LLM judge (rubric)',
		structural: 'Tool call check'
	};

	let suiteList = $state<EvalSuite[]>([]);
	let agentList = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let loadError = $state<string | null>(null);

	async function refresh() {
		loadError = null;
		try {
			[suiteList, agentList, workflowList] = await Promise.all([
				evals.listSuites(),
				agents.list(),
				workflows.list()
			]);
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load eval suites';
		}
	}

	refresh();

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
			createSuiteError = err instanceof Error ? err.message : 'Failed to create eval suite';
		} finally {
			createSuiteBusy = false;
		}
	}

	async function handleDeleteSuite(suiteId: string) {
		try {
			await evals.deleteSuite(suiteId);
			await refresh();
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to delete eval suite';
		}
	}

	// --- Case management (one suite expanded at a time) ---

	let expandedCasesSuiteId = $state<string | null>(null);
	let casesBySuite = $state<Record<string, EvalCase[]>>({});
	let casesError = $state<string | null>(null);

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

	async function toggleCases(suiteId: string) {
		if (expandedCasesSuiteId === suiteId) {
			expandedCasesSuiteId = null;
			return;
		}
		expandedCasesSuiteId = suiteId;
		showAddCase = false;
		casesError = null;
		try {
			casesBySuite[suiteId] = await evals.listCases(suiteId);
		} catch (err) {
			casesError = err instanceof Error ? err.message : 'Failed to load cases';
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

	async function handleAddCase(suiteId: string) {
		if (!caseNameDraft.trim() || !caseInputDraft.trim()) return;
		let expectedToolArgs: Record<string, unknown> | undefined;
		if (caseJudgeTypeDraft === 'structural' && caseToolArgsDraft.trim()) {
			try {
				expectedToolArgs = JSON.parse(caseToolArgsDraft) as Record<string, unknown>;
			} catch {
				addCaseError = 'Expected tool args must be valid JSON (or left blank).';
				return;
			}
		}
		addCaseBusy = true;
		addCaseError = null;
		try {
			await evals.createCase(suiteId, {
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
			casesBySuite[suiteId] = await evals.listCases(suiteId);
			await refresh();
		} catch (err) {
			addCaseError = err instanceof Error ? err.message : 'Failed to add case';
		} finally {
			addCaseBusy = false;
		}
	}

	async function handleDeleteCase(suiteId: string, caseId: string) {
		try {
			await evals.deleteCase(suiteId, caseId);
			casesBySuite[suiteId] = await evals.listCases(suiteId);
			await refresh();
		} catch (err) {
			casesError = err instanceof Error ? err.message : 'Failed to delete case';
		}
	}

	// --- Running suites + run history ---

	let runBusy = $state<Record<string, boolean>>({});
	let runError = $state<Record<string, string | null>>({});
	let expandedRunsSuiteId = $state<string | null>(null);
	let runsBySuite = $state<Record<string, EvalRun[]>>({});
	let runsError = $state<string | null>(null);
	let expandedRunId = $state<string | null>(null);
	let resultsByRun = $state<Record<string, EvalCaseResult[]>>({});
	let resultsError = $state<string | null>(null);

	async function handleRunSuite(suiteId: string) {
		runBusy[suiteId] = true;
		runError[suiteId] = null;
		try {
			await evals.run(suiteId);
			expandedRunsSuiteId = suiteId;
			runsBySuite[suiteId] = await evals.listRuns(suiteId);
		} catch (err) {
			runError[suiteId] = err instanceof Error ? err.message : 'Failed to run eval suite';
		} finally {
			runBusy[suiteId] = false;
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
		} catch (err) {
			runsError = err instanceof Error ? err.message : 'Failed to load run history';
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
		} catch (err) {
			resultsError = err instanceof Error ? err.message : 'Failed to load results';
		}
	}

	function resultStatusClass(status: string): string {
		if (status === 'passed')
			return 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400';
		if (status === 'failed')
			return 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400';
		return 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400';
	}

	function runSummaryClass(run: EvalRun): string {
		if (run.pass_count === run.case_count) {
			return 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400';
		}
		if (run.fail_count > 0) {
			return 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400';
		}
		return 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400';
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<header class="flex items-start justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">Evals</h1>
			<p class="text-sm text-neutral-600 dark:text-neutral-400">
				Regression-test suites for agents and workflows — a fixed set of inputs, each judged against
				an expected outcome, so a behavior change shows up as a failing case instead of a human
				happening to notice a bad reply.
			</p>
		</div>
		<button
			onclick={openCreateSuite}
			class="shrink-0 rounded-md bg-agent-cyan px-3 py-1.5 text-sm font-semibold text-white hover:bg-agent-cyan-600"
		>
			+ New suite
		</button>
	</header>

	{#if showCreateSuite}
		<div class="flex flex-col gap-2 rounded-lg border border-ink/12 p-4 dark:border-white/10">
			<label class="flex flex-col gap-1 text-xs text-neutral-500">
				Name
				<input
					type="text"
					bind:value={suiteNameDraft}
					placeholder="greeting-regressions"
					class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			</label>
			<label class="flex flex-col gap-1 text-xs text-neutral-500">
				Description
				<input
					type="text"
					bind:value={suiteDescriptionDraft}
					placeholder="optional"
					class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
				/>
			</label>
			<div class="flex gap-4 text-xs text-neutral-500">
				<label class="flex items-center gap-1.5">
					<input
						type="radio"
						name="subject-type"
						value="agent"
						checked={suiteSubjectType === 'agent'}
						onchange={() => {
							suiteSubjectType = 'agent';
							suiteSubjectId = '';
						}}
					/>
					Agent
				</label>
				<label class="flex items-center gap-1.5">
					<input
						type="radio"
						name="subject-type"
						value="workflow"
						checked={suiteSubjectType === 'workflow'}
						onchange={() => {
							suiteSubjectType = 'workflow';
							suiteSubjectId = '';
						}}
					/>
					Workflow
				</label>
			</div>
			<select
				bind:value={suiteSubjectId}
				class="w-fit rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			>
				<option value="" disabled>
					{suiteSubjectType === 'agent' ? 'Select an agent…' : 'Select a workflow…'}
				</option>
				{#if suiteSubjectType === 'agent'}
					{#each agentList as agent (agent.id)}
						<option value={agent.id}>{agent.name}</option>
					{/each}
				{:else}
					{#each workflowList as workflow (workflow.id)}
						<option value={workflow.id}>/{workflow.name}</option>
					{/each}
				{/if}
			</select>
			{#if createSuiteError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{createSuiteError}</p>
			{/if}
			<div class="flex gap-3">
				<button
					onclick={handleCreateSuite}
					disabled={createSuiteBusy || !suiteNameDraft.trim() || !suiteSubjectId}
					class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-sm font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
				>
					{createSuiteBusy ? 'Creating…' : 'Create suite'}
				</button>
				<button
					onclick={() => (showCreateSuite = false)}
					class="text-sm text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
				>
					Cancel
				</button>
			</div>
		</div>
	{/if}

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if suiteList.length === 0 && !showCreateSuite}
		<p class="text-sm text-neutral-500 italic">
			No eval suites yet — create one to start catching regressions.
		</p>
	{:else}
		<ul class="flex flex-col gap-3">
			{#each suiteList as suite (suite.id)}
				<li class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					<div class="flex items-start justify-between gap-3">
						<div>
							<p class="flex items-center gap-2 font-medium text-ink dark:text-ink-dark">
								<Icon name="flask" class="h-4 w-4 flex-none text-neutral-500" />
								{suite.name}
							</p>
							<p class="text-xs text-neutral-500">
								{suite.subject_type === 'agent' ? '' : '/'}{suite.subject_name}
								· {suite.case_count} case{suite.case_count === 1 ? '' : 's'}
							</p>
							{#if suite.description}
								<p class="mt-1 text-xs text-neutral-500">{suite.description}</p>
							{/if}
						</div>
						<div class="flex flex-none items-center gap-2">
							<button
								onclick={() => handleRunSuite(suite.id)}
								disabled={runBusy[suite.id] || suite.case_count === 0}
								class="rounded-md border border-ink/15 px-2.5 py-1 text-xs text-ink disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
							>
								{runBusy[suite.id] ? 'Running…' : 'Run'}
							</button>
							<button
								onclick={() => handleDeleteSuite(suite.id)}
								class="text-xs text-neutral-500 hover:text-agent-magenta-600"
							>
								Delete
							</button>
						</div>
					</div>
					{#if runError[suite.id]}
						<p class="mt-2 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
							{runError[suite.id]}
						</p>
					{/if}

					<div class="mt-3 flex gap-4 text-xs">
						<button
							onclick={() => toggleCases(suite.id)}
							class="text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							{expandedCasesSuiteId === suite.id ? 'Hide cases' : 'Manage cases'}
						</button>
						<button
							onclick={() => toggleRuns(suite.id)}
							class="text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							{expandedRunsSuiteId === suite.id ? 'Hide runs' : 'Run history'}
						</button>
					</div>

					{#if expandedCasesSuiteId === suite.id}
						<div class="mt-3 flex flex-col gap-2 border-t border-ink/10 pt-3 dark:border-white/10">
							{#if casesError}
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									{casesError}
								</p>
							{:else if !casesBySuite[suite.id]}
								<p class="text-xs text-neutral-500">Loading cases…</p>
							{:else if casesBySuite[suite.id].length === 0 && !showAddCase}
								<p class="text-xs text-neutral-500">No cases yet.</p>
							{:else}
								<ul class="flex flex-col gap-1.5">
									{#each casesBySuite[suite.id] as evalCase (evalCase.id)}
										<li
											class="flex items-center justify-between gap-2 rounded-md border border-ink/10 px-2.5 py-1.5 text-xs dark:border-white/10"
										>
											<div>
												<span class="font-medium text-ink dark:text-ink-dark">{evalCase.name}</span>
												<span
													class="ml-1.5 rounded-sm bg-neutral-200/60 px-1.5 py-0.5 text-[11px] text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
												>
													{JUDGE_TYPE_LABELS[evalCase.judge_type]}
												</span>
											</div>
											<button
												onclick={() => handleDeleteCase(suite.id, evalCase.id)}
												class="text-neutral-500 hover:text-agent-magenta-600"
											>
												Remove
											</button>
										</li>
									{/each}
								</ul>
							{/if}

							{#if showAddCase}
								<div
									class="flex flex-col gap-2 rounded-md border border-ink/12 p-3 dark:border-white/10"
								>
									<label class="flex flex-col gap-1 text-xs text-neutral-500">
										Case name
										<input
											type="text"
											bind:value={caseNameDraft}
											class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
										/>
									</label>
									<label class="flex flex-col gap-1 text-xs text-neutral-500">
										Input
										<textarea
											bind:value={caseInputDraft}
											rows="2"
											class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
										></textarea>
									</label>
									<label class="flex flex-col gap-1 text-xs text-neutral-500">
										Judge
										<select
											bind:value={caseJudgeTypeDraft}
											class="w-fit rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
										>
											<option value="exact">Exact match</option>
											<option value="substring">Contains text</option>
											<option value="llm_judge">LLM judge (rubric)</option>
											{#if suite.subject_type === 'agent'}
												<option value="structural">Tool call check</option>
											{/if}
										</select>
									</label>
									{#if caseJudgeTypeDraft === 'exact' || caseJudgeTypeDraft === 'substring'}
										<label class="flex flex-col gap-1 text-xs text-neutral-500">
											Expected output
											<textarea
												bind:value={caseExpectedOutputDraft}
												rows="2"
												class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
											></textarea>
										</label>
									{:else if caseJudgeTypeDraft === 'llm_judge'}
										<label class="flex flex-col gap-1 text-xs text-neutral-500">
											Rubric
											<textarea
												bind:value={caseRubricDraft}
												rows="2"
												placeholder="The reply should acknowledge the request and offer a next step."
												class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
											></textarea>
										</label>
									{:else if caseJudgeTypeDraft === 'structural'}
										<label class="flex flex-col gap-1 text-xs text-neutral-500">
											Expected tool name
											<input
												type="text"
												bind:value={caseToolNameDraft}
												placeholder="search"
												class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
											/>
										</label>
										<label class="flex flex-col gap-1 text-xs text-neutral-500">
											Expected args (JSON, optional — leave blank to only check the tool was called)
											<input
												type="text"
												bind:value={caseToolArgsDraft}
												placeholder={'{"query": "cats"}'}
												class="rounded-md border border-ink/15 bg-transparent px-2 py-1 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
											/>
										</label>
									{/if}
									{#if addCaseError}
										<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
											{addCaseError}
										</p>
									{/if}
									<div class="flex gap-3">
										<button
											onclick={() => handleAddCase(suite.id)}
											disabled={addCaseBusy || !caseNameDraft.trim() || !caseInputDraft.trim()}
											class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
										>
											{addCaseBusy ? 'Adding…' : 'Add case'}
										</button>
										<button
											onclick={() => (showAddCase = false)}
											class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
										>
											Cancel
										</button>
									</div>
								</div>
							{:else}
								<button
									onclick={openAddCase}
									class="self-start text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									+ Add case
								</button>
							{/if}
						</div>
					{/if}

					{#if expandedRunsSuiteId === suite.id}
						<div class="mt-3 flex flex-col gap-2 border-t border-ink/10 pt-3 dark:border-white/10">
							{#if runsError}
								<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
									{runsError}
								</p>
							{:else if !runsBySuite[suite.id]}
								<p class="text-xs text-neutral-500">Loading runs…</p>
							{:else if runsBySuite[suite.id].length === 0}
								<p class="text-xs text-neutral-500">No runs yet — click "Run" to try this suite.</p>
							{:else}
								<ul class="flex flex-col gap-2">
									{#each runsBySuite[suite.id] as run (run.id)}
										<li class="rounded-lg border border-ink/12 dark:border-white/10">
											<button
												type="button"
												onclick={() => toggleRunResults(suite.id, run.id)}
												class="flex w-full items-center justify-between px-3 py-2 text-left text-xs"
											>
												<span class="flex items-center gap-2 text-ink dark:text-ink-dark">
													<Icon
														name="chevron"
														class="h-3 w-3 flex-none text-neutral-500 transition-transform duration-150 {expandedRunId ===
														run.id
															? 'rotate-90'
															: ''}"
													/>
													{timeAgo(run.started_at)}
												</span>
												<span class="rounded-sm px-2 py-0.5 {runSummaryClass(run)}">
													{run.pass_count}/{run.case_count} passed
												</span>
											</button>
											{#if expandedRunId === run.id}
												<div class="border-t border-ink/10 px-3 py-2 dark:border-white/10">
													{#if resultsError}
														<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
															{resultsError}
														</p>
													{:else if !resultsByRun[run.id]}
														<p class="text-xs text-neutral-500">Loading results…</p>
													{:else}
														<ul class="flex flex-col gap-2">
															{#each resultsByRun[run.id] as result (result.id)}
																{@const caseName =
																	casesBySuite[suite.id]?.find((c) => c.id === result.case_id)
																		?.name ?? 'Deleted case'}
																<li class="flex flex-col gap-1 text-xs">
																	<div class="flex items-center gap-2">
																		<span
																			class="rounded-sm px-1.5 py-0.5 {resultStatusClass(
																				result.status
																			)}"
																		>
																			{result.status}
																		</span>
																		<span class="text-neutral-500">{caseName}</span>
																		{#if result.score !== null}
																			<span class="text-neutral-500"
																				>score {result.score.toFixed(2)}</span
																			>
																		{/if}
																	</div>
																	{#if result.actual_output}
																		<p class="text-neutral-600 dark:text-neutral-400">
																			{result.actual_output}
																		</p>
																	{/if}
																	{#if result.judge_reasoning}
																		<p class="text-neutral-500 italic">{result.judge_reasoning}</p>
																	{/if}
																	{#if result.error_message}
																		<p class="text-agent-magenta-700 dark:text-agent-magenta-400">
																			{result.error_message}
																		</p>
																	{/if}
																</li>
															{/each}
														</ul>
													{/if}
												</div>
											{/if}
										</li>
									{/each}
								</ul>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>
