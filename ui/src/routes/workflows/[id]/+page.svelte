<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		workflows,
		type Workflow,
		type WorkflowNode,
		type WorkflowConnection,
		type WorkflowRun,
		type WorkflowNodeRun,
		type WorkflowSchedule
	} from '$lib/api/workflows';
	import { agents as agentsApi, type Agent } from '$lib/api/agents';
	import { channels as channelsApi, type Channel } from '$lib/api/channels';
	import { timeAgo } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';
	import WorkflowNodeForm, {
		type WorkflowNodeFormValues
	} from '$lib/components/WorkflowNodeForm.svelte';

	const NODE_TYPE_LABELS: Record<WorkflowNode['node_type'], string> = {
		agent: 'Agent',
		transform: 'Transform',
		summarize: 'Summarize',
		conditional: 'Conditional',
		merge: 'Merge',
		human_input: 'Human input',
		workflow: 'Workflow'
	};

	let workflow = $state<Workflow | null>(null);
	let nodeList = $state<WorkflowNode[]>([]);
	let connectionList = $state<WorkflowConnection[]>([]);
	let agentList = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let channelList = $state<Channel[]>([]);
	let scheduleList = $state<WorkflowSchedule[]>([]);
	let loadError = $state<string | null>(null);

	// A workflow embedding itself is always a cycle -- excluded from the
	// picker so there's no reason to even offer it (the engine's own
	// ancestry guard still catches it if something else got past this).
	const workflowOptions = $derived(workflowList.filter((w) => w.id !== workflow?.id));

	let renaming = $state(false);
	let nameDraft = $state('');
	let descriptionDraft = $state('');
	let renameError = $state<string | null>(null);

	let publishBusy = $state(false);
	let publishError = $state<string | null>(null);

	// `undefined` = no insert form open; `null` = inserting as the new entry
	// (before the current first step); a node id = inserting right after it.
	let insertAfter = $state<string | null | undefined>(undefined);
	let insertBusy = $state(false);
	let insertError = $state<string | null>(null);
	let insertFormKey = $state(0);

	let editingNodeId = $state<string | null>(null);
	let editBusy = $state(false);
	let editError = $state<string | null>(null);

	let actionError = $state<string | null>(null);

	let runList = $state<WorkflowRun[] | null>(null);
	let runsError = $state<string | null>(null);
	let expandedRunId = $state<string | null>(null);
	let nodeRunsByRun = $state<Record<string, WorkflowNodeRun[]>>({});
	let nodeRunsError = $state<string | null>(null);

	const chain = $derived(buildChain(nodeList, connectionList));
	const orphanNodes = $derived(nodeList.filter((n) => !chain.some((c) => c.id === n.id)));

	function buildChain(nodes: WorkflowNode[], connections: WorkflowConnection[]): WorkflowNode[] {
		const nodeById = new Map(nodes.map((n) => [n.id, n]));
		const outgoingFrom = new Map(connections.map((c) => [c.from_node_id, c]));
		const result: WorkflowNode[] = [];
		let cursor = outgoingFrom.get(null);
		while (cursor) {
			const node = nodeById.get(cursor.to_node_id);
			if (!node || result.some((n) => n.id === node.id)) break;
			result.push(node);
			cursor = outgoingFrom.get(node.id);
		}
		return result;
	}

	async function load(workflowId: string) {
		loadError = null;
		try {
			const [
				loadedWorkflow,
				loadedNodes,
				loadedConnections,
				loadedAgents,
				loadedWorkflows,
				loadedChannels,
				loadedSchedules
			] = await Promise.all([
				workflows.get(workflowId),
				workflows.listNodes(workflowId),
				workflows.listConnections(workflowId),
				agentsApi.list(),
				workflows.list(),
				channelsApi.list(),
				workflows.listSchedules(workflowId)
			]);
			workflow = loadedWorkflow;
			nodeList = loadedNodes;
			connectionList = loadedConnections;
			agentList = loadedAgents;
			workflowList = loadedWorkflows;
			channelList = loadedChannels;
			scheduleList = loadedSchedules;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Failed to load workflow';
		}
	}

	$effect(() => {
		load(page.params.id!);
	});

	function startRename() {
		if (!workflow) return;
		renameError = null;
		nameDraft = workflow.name;
		descriptionDraft = workflow.description ?? '';
		renaming = true;
	}

	async function saveRename() {
		if (!workflow) return;
		renameError = null;
		try {
			workflow = await workflows.update(workflow.id, {
				name: nameDraft.trim(),
				description: descriptionDraft.trim() || null
			});
			renaming = false;
		} catch (err) {
			renameError = err instanceof Error ? err.message : 'Failed to rename workflow';
		}
	}

	async function togglePublish() {
		if (!workflow) return;
		publishError = null;
		publishBusy = true;
		try {
			workflow = workflow.published
				? await workflows.unpublish(workflow.id)
				: await workflows.publish(workflow.id);
		} catch (err) {
			publishError = err instanceof Error ? err.message : 'Failed to update publish state';
		} finally {
			publishBusy = false;
		}
	}

	let showAddSchedule = $state(false);
	let scheduleCronDraft = $state('');
	let scheduleChannelDraft = $state('');
	let scheduleInputDraft = $state('');
	let schedulePreview = $state<{ next_fire_at: string | null; error: string | null } | null>(null);
	let scheduleBusy = $state(false);
	let scheduleError = $state<string | null>(null);
	let schedulePreviewTimer: ReturnType<typeof setTimeout> | undefined;

	function openAddSchedule() {
		showAddSchedule = true;
		scheduleCronDraft = '';
		scheduleChannelDraft = channelList[0]?.id ?? '';
		scheduleInputDraft = '';
		schedulePreview = null;
		scheduleError = null;
	}

	function closeAddSchedule() {
		showAddSchedule = false;
		clearTimeout(schedulePreviewTimer);
	}

	onDestroy(() => clearTimeout(schedulePreviewTimer));

	function onCronDraftChange() {
		schedulePreview = null;
		clearTimeout(schedulePreviewTimer);
		if (!workflow || !scheduleCronDraft.trim()) return;
		const workflowId = workflow.id;
		const cronExpression = scheduleCronDraft;
		schedulePreviewTimer = setTimeout(async () => {
			try {
				schedulePreview = await workflows.previewSchedule(workflowId, cronExpression);
			} catch (err) {
				schedulePreview = {
					next_fire_at: null,
					error: err instanceof Error ? err.message : 'Failed to preview schedule'
				};
			}
		}, 400);
	}

	async function handleAddSchedule() {
		if (!workflow || !scheduleChannelDraft) return;
		const workflowId = workflow.id;
		scheduleError = null;
		scheduleBusy = true;
		try {
			await workflows.createSchedule(workflowId, {
				channel_id: scheduleChannelDraft,
				cron_expression: scheduleCronDraft,
				input_content: scheduleInputDraft
			});
			showAddSchedule = false;
			scheduleList = await workflows.listSchedules(workflowId);
		} catch (err) {
			scheduleError = err instanceof Error ? err.message : 'Failed to create schedule';
		} finally {
			scheduleBusy = false;
		}
	}

	// #93: a schedule an agent creates via the schedule_workflow tool has
	// no cron_expression (it's a one-off run_once fire time) and starts
	// disabled with created_by set to the agent's id, not 'human' --
	// pending human approval before toggleScheduleEnabled can turn it on.
	function scheduleTiming(schedule: WorkflowSchedule): string {
		return schedule.run_once
			? `once at ${new Date(schedule.next_fire_at).toLocaleString()}`
			: (schedule.cron_expression ?? '');
	}

	function isPendingAgentApproval(schedule: WorkflowSchedule): boolean {
		return schedule.created_by !== 'human' && !schedule.enabled && !schedule.last_fired_at;
	}

	// A one-off already fired can't be turned back on (api/workflows.py's
	// update_schedule rejects it — its next_fire_at is necessarily in the
	// past, so re-enabling would fire the same reminder again immediately).
	function isSpentOneOff(schedule: WorkflowSchedule): boolean {
		return schedule.run_once && schedule.last_fired_at !== null;
	}

	async function toggleScheduleEnabled(schedule: WorkflowSchedule) {
		if (!workflow) return;
		const workflowId = workflow.id;
		scheduleError = null;
		try {
			const updated = await workflows.updateSchedule(workflowId, schedule.id, {
				enabled: !schedule.enabled
			});
			scheduleList = scheduleList.map((s) => (s.id === updated.id ? updated : s));
		} catch (err) {
			scheduleError = err instanceof Error ? err.message : 'Failed to update schedule';
		}
	}

	async function removeSchedule(scheduleId: string) {
		if (!workflow) return;
		const workflowId = workflow.id;
		scheduleError = null;
		try {
			await workflows.removeSchedule(workflowId, scheduleId);
			scheduleList = scheduleList.filter((s) => s.id !== scheduleId);
		} catch (err) {
			scheduleError = err instanceof Error ? err.message : 'Failed to remove schedule';
		}
	}

	function openInsertForm(afterNodeId: string | null) {
		insertError = null;
		insertAfter = afterNodeId;
		insertFormKey += 1;
	}

	function closeInsertForm() {
		insertAfter = undefined;
		insertError = null;
	}

	async function handleInsert(values: WorkflowNodeFormValues) {
		if (!workflow || insertAfter === undefined) return;
		const workflowId = workflow.id;
		const position = insertAfter;
		insertError = null;
		insertBusy = true;
		try {
			const node = await workflows.createNode(workflowId, values);
			const displaced = connectionList.find((c) => c.from_node_id === position);
			if (displaced) {
				await workflows.removeConnection(workflowId, displaced.id);
				await workflows.createConnection(workflowId, {
					from_node_id: position,
					to_node_id: node.id
				});
				await workflows.createConnection(workflowId, {
					from_node_id: node.id,
					to_node_id: displaced.to_node_id
				});
			} else {
				await workflows.createConnection(workflowId, {
					from_node_id: position,
					to_node_id: node.id
				});
			}
			insertAfter = undefined;
			await load(workflowId);
		} catch (err) {
			insertError = err instanceof Error ? err.message : 'Failed to add step';
		} finally {
			insertBusy = false;
		}
	}

	function startEditNode(nodeId: string) {
		editError = null;
		editingNodeId = nodeId;
	}

	async function handleEditNode(nodeId: string, values: WorkflowNodeFormValues) {
		if (!workflow) return;
		editError = null;
		editBusy = true;
		try {
			await workflows.updateNode(workflow.id, nodeId, {
				name: values.name,
				agent_id: values.node_type === 'agent' ? values.agent_id : undefined,
				child_workflow_id: values.node_type === 'workflow' ? values.child_workflow_id : undefined,
				config: values.config,
				retry_max_attempts: values.retry_max_attempts,
				retry_backoff_seconds: values.retry_backoff_seconds
			});
			editingNodeId = null;
			await load(workflow.id);
		} catch (err) {
			editError = err instanceof Error ? err.message : 'Failed to update step';
		} finally {
			editBusy = false;
		}
	}

	async function handleRemoveNode(nodeId: string) {
		if (!workflow) return;
		const workflowId = workflow.id;
		actionError = null;
		try {
			const incoming = connectionList.find((c) => c.to_node_id === nodeId);
			const outgoing = connectionList.find((c) => c.from_node_id === nodeId);
			await workflows.removeNode(workflowId, nodeId);
			if (incoming && outgoing) {
				await workflows.createConnection(workflowId, {
					from_node_id: incoming.from_node_id,
					to_node_id: outgoing.to_node_id
				});
			}
			await load(workflowId);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to remove step';
		}
	}

	async function connectOrphanToEnd(nodeId: string) {
		if (!workflow) return;
		const workflowId = workflow.id;
		actionError = null;
		try {
			await workflows.createConnection(workflowId, {
				from_node_id: chain.length > 0 ? chain[chain.length - 1].id : null,
				to_node_id: nodeId
			});
			await load(workflowId);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Failed to connect step';
		}
	}

	async function loadRuns() {
		if (!workflow) return;
		runsError = null;
		try {
			runList = await workflows.listRuns(workflow.id);
		} catch (err) {
			runsError = err instanceof Error ? err.message : 'Failed to load run history';
		}
	}

	async function toggleRun(runId: string) {
		if (!workflow) return;
		if (expandedRunId === runId) {
			expandedRunId = null;
			return;
		}
		expandedRunId = runId;
		if (nodeRunsByRun[runId]) return;
		nodeRunsError = null;
		try {
			nodeRunsByRun[runId] = await workflows.listNodeRuns(workflow.id, runId);
		} catch (err) {
			nodeRunsError = err instanceof Error ? err.message : 'Failed to load step history';
		}
	}

	function statusClass(status: string): string {
		if (status === 'completed')
			return 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400';
		if (status === 'failed')
			return 'bg-agent-magenta-100 text-agent-magenta-700 dark:bg-agent-magenta-900/30 dark:text-agent-magenta-400';
		if (status === 'awaiting_human')
			return 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400';
		return 'bg-neutral-200 text-neutral-700 dark:bg-white/10 dark:text-neutral-300';
	}
</script>

<div class="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
	<a
		href={resolve('/workflows')}
		class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
	>
		&larr; Workflows
	</a>

	{#if loadError}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{loadError}</p>
	{:else if !workflow}
		<p class="text-sm text-neutral-500">Loading…</p>
	{:else}
		<header class="flex flex-col gap-2">
			{#if renaming}
				<div class="flex flex-col gap-2">
					<input
						type="text"
						bind:value={nameDraft}
						class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-lg font-semibold text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					<input
						type="text"
						bind:value={descriptionDraft}
						placeholder="Description"
						class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
					/>
					{#if renameError}
						<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{renameError}</p>
					{/if}
					<div class="flex gap-3">
						<button
							onclick={saveRename}
							class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-sm font-semibold text-white hover:bg-agent-cyan-600"
						>
							Save
						</button>
						<button
							onclick={() => (renaming = false)}
							class="text-sm text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							Cancel
						</button>
					</div>
				</div>
			{:else}
				<div class="flex items-start justify-between">
					<div>
						<h1 class="flex items-center gap-2 text-2xl font-semibold text-ink dark:text-ink-dark">
							<span class="text-neutral-500">/</span>{workflow.name}
							<span
								class="rounded-sm px-1.5 py-0.5 text-xs font-normal {workflow.published
									? 'bg-agent-cyan-100 text-agent-cyan-700 dark:bg-agent-cyan-900/30 dark:text-agent-cyan-400'
									: 'bg-neutral-200 text-neutral-700 dark:bg-white/10 dark:text-neutral-300'}"
							>
								{workflow.published ? 'Published' : 'Draft'}
							</span>
						</h1>
						{#if workflow.description}
							<p class="text-sm text-neutral-600 dark:text-neutral-400">{workflow.description}</p>
						{/if}
					</div>
					<div class="flex flex-none items-center gap-2">
						<button
							onclick={togglePublish}
							disabled={publishBusy}
							class="rounded-md border border-ink/15 px-2.5 py-1 text-xs text-ink disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
						>
							{#if publishBusy}
								…
							{:else}
								{workflow.published ? 'Unpublish' : 'Publish'}
							{/if}
						</button>
						<button
							onclick={startRename}
							class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							Edit
						</button>
					</div>
				</div>
				{#if publishError}
					<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{publishError}</p>
				{/if}
				<p class="font-mono text-xs text-neutral-500">
					{#if workflow.published}
						Trigger from any channel: /{workflow.name} &lt;input&gt;
					{:else}
						Publish this workflow to trigger it with /{workflow.name} &lt;input&gt;
					{/if}
				</p>
			{/if}
		</header>

		<section class="flex flex-col gap-3 rounded-lg border border-ink/12 p-4 dark:border-white/10">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Schedules</h2>
				<button
					onclick={openAddSchedule}
					class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
				>
					+ Add schedule
				</button>
			</div>
			{#if !workflow.published}
				<p class="text-xs text-neutral-500">
					Schedules won't fire until this workflow is published.
				</p>
			{/if}
			{#if scheduleError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{scheduleError}</p>
			{/if}
			{#if scheduleList.length === 0 && !showAddSchedule}
				<p class="text-sm text-neutral-500">No schedules configured.</p>
			{:else}
				<ul class="flex flex-col gap-2">
					{#each scheduleList as schedule (schedule.id)}
						<li
							class="flex flex-col gap-1 rounded-md border border-ink/12 p-2 text-xs dark:border-white/10"
						>
							<div class="flex items-center justify-between">
								<span class="font-mono text-sm text-ink dark:text-ink-dark">
									{scheduleTiming(schedule)}
									{#if schedule.name}
										<span class="font-sans text-neutral-500">({schedule.name})</span>
									{/if}
								</span>
								<div class="flex items-center gap-2">
									{#if schedule.enabled || !isSpentOneOff(schedule)}
										<button
											onclick={() => toggleScheduleEnabled(schedule)}
											class="text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
										>
											{schedule.enabled ? 'Disable' : 'Enable'}
										</button>
									{/if}
									<button
										onclick={() => removeSchedule(schedule.id)}
										class="text-neutral-500 hover:text-agent-magenta-600"
									>
										Remove
									</button>
								</div>
							</div>
							{#if isPendingAgentApproval(schedule)}
								<p class="text-amber-700 dark:text-amber-400">
									⏳ Created by an agent — pending your approval before it can fire
								</p>
							{/if}
							<p class="text-neutral-500">
								Channel: {channelList.find((c) => c.id === schedule.channel_id)?.name ??
									'Deleted channel'}
							</p>
							{#if schedule.enabled}
								<p class="text-neutral-500">
									Next run: {new Date(schedule.next_fire_at).toLocaleString()}
								</p>
							{/if}
							<p class="text-neutral-500">
								Last fired: {schedule.last_fired_at ? timeAgo(schedule.last_fired_at) : 'never'}
							</p>
							{#if !schedule.enabled && schedule.consecutive_failures >= 5}
								<p class="text-agent-magenta-700 dark:text-agent-magenta-400">
									Disabled after {schedule.consecutive_failures} consecutive failures
								</p>
							{:else if schedule.consecutive_failures > 0}
								<p class="text-amber-700 dark:text-amber-400">
									⚠ {schedule.consecutive_failures} consecutive failure{schedule.consecutive_failures ===
									1
										? ''
										: 's'}
								</p>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if showAddSchedule}
				<div class="flex flex-col gap-2 rounded-md border border-ink/12 p-3 dark:border-white/10">
					<label class="flex flex-col gap-1 text-xs text-neutral-500">
						Cron expression
						<input
							type="text"
							bind:value={scheduleCronDraft}
							oninput={onCronDraftChange}
							placeholder="0 9 * * *"
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					</label>
					{#if schedulePreview}
						{#if schedulePreview.error}
							<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
								{schedulePreview.error}
							</p>
						{:else if schedulePreview.next_fire_at}
							<p class="text-xs text-neutral-500">
								Next run: {new Date(schedulePreview.next_fire_at).toLocaleString()}
							</p>
						{/if}
					{/if}
					<label class="flex flex-col gap-1 text-xs text-neutral-500">
						Channel
						<select
							bind:value={scheduleChannelDraft}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						>
							{#each channelList as channel (channel.id)}
								<option value={channel.id}>{channel.name}</option>
							{/each}
						</select>
					</label>
					<label class="flex flex-col gap-1 text-xs text-neutral-500">
						Input
						<input
							type="text"
							bind:value={scheduleInputDraft}
							placeholder="input passed to the entry step"
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					</label>
					{#if scheduleError}
						<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
							{scheduleError}
						</p>
					{/if}
					<div class="flex gap-3">
						<button
							onclick={handleAddSchedule}
							disabled={scheduleBusy || !scheduleCronDraft.trim() || !scheduleChannelDraft}
							class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
						>
							{scheduleBusy ? 'Adding…' : 'Add schedule'}
						</button>
						<button
							onclick={closeAddSchedule}
							class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							Cancel
						</button>
					</div>
				</div>
			{/if}
		</section>

		{#if actionError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{actionError}</p>
		{/if}

		<section class="flex flex-col items-stretch gap-0">
			<button
				type="button"
				onclick={() => openInsertForm(null)}
				class="mx-auto rounded-full border border-dashed border-ink/25 px-3 py-1 text-xs text-neutral-500 hover:border-agent-cyan-600 hover:text-agent-cyan-700 dark:border-white/20 dark:hover:text-agent-cyan-400"
			>
				+ Add step
			</button>
			{#if insertAfter === null}
				<div
					class="my-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
				>
					{#key insertFormKey}
						<WorkflowNodeForm
							agentOptions={agentList}
							{workflowOptions}
							submitLabel="Add step"
							busyLabel="Adding…"
							busy={insertBusy}
							error={insertError}
							onsubmit={handleInsert}
							oncancel={closeInsertForm}
						/>
					{/key}
				</div>
			{/if}

			{#each chain as node, i (node.id)}
				<div class="flex justify-center py-1">
					<div class="h-4 w-px bg-ink/15 dark:bg-white/15"></div>
				</div>
				<div class="rounded-lg border border-ink/12 p-4 dark:border-white/10">
					{#if editingNodeId === node.id}
						{#key node.id}
							<WorkflowNodeForm
								agentOptions={agentList}
								{workflowOptions}
								lockNodeType
								initial={{
									name: node.name,
									node_type: node.node_type,
									agent_id: node.agent_id,
									child_workflow_id: node.child_workflow_id,
									config: node.config,
									retry_max_attempts: node.retry_max_attempts,
									retry_backoff_seconds: node.retry_backoff_seconds
								}}
								submitLabel="Save changes"
								busyLabel="Saving…"
								busy={editBusy}
								error={editError}
								onsubmit={(values) => handleEditNode(node.id, values)}
								oncancel={() => (editingNodeId = null)}
							/>
						{/key}
					{:else}
						<div class="flex items-start justify-between">
							<div>
								<span
									class="rounded-sm bg-neutral-200 px-1.5 py-0.5 text-[11px] font-medium text-neutral-700 dark:bg-white/10 dark:text-neutral-300"
								>
									{i + 1}. {NODE_TYPE_LABELS[node.node_type]}
								</span>
								<p class="mt-1 font-medium text-ink dark:text-ink-dark">{node.name}</p>
								{#if node.node_type === 'agent'}
									<p class="text-xs text-neutral-500">
										{agentList.find((a) => a.id === node.agent_id)?.name ?? 'Deleted agent'}
									</p>
								{:else if node.node_type === 'transform' && node.config.template}
									<p class="font-mono text-xs text-neutral-500">
										{node.config.template}
									</p>
								{:else if node.node_type === 'conditional' && node.config.contains}
									<p class="text-xs text-neutral-500">
										stop unless input contains "{node.config.contains}"
									</p>
								{:else if node.node_type === 'workflow'}
									<p class="text-xs text-neutral-500">
										/{workflowList.find((w) => w.id === node.child_workflow_id)?.name ??
											'Deleted workflow'}
									</p>
								{/if}
							</div>
							<div class="flex items-center gap-2">
								<button
									onclick={() => startEditNode(node.id)}
									class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									Edit
								</button>
								<button
									onclick={() => handleRemoveNode(node.id)}
									class="text-xs text-neutral-500 hover:text-agent-magenta-600"
								>
									Remove
								</button>
							</div>
						</div>
					{/if}
				</div>
				<div class="flex justify-center py-1">
					<div class="h-4 w-px bg-ink/15 dark:bg-white/15"></div>
				</div>
				<button
					type="button"
					onclick={() => openInsertForm(node.id)}
					class="mx-auto rounded-full border border-dashed border-ink/25 px-3 py-1 text-xs text-neutral-500 hover:border-agent-cyan-600 hover:text-agent-cyan-700 dark:border-white/20 dark:hover:text-agent-cyan-400"
				>
					+ Add step
				</button>
				{#if insertAfter === node.id}
					<div
						class="my-3 rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
					>
						{#key insertFormKey}
							<WorkflowNodeForm
								agentOptions={agentList}
								{workflowOptions}
								submitLabel="Add step"
								busyLabel="Adding…"
								busy={insertBusy}
								error={insertError}
								onsubmit={handleInsert}
								oncancel={closeInsertForm}
							/>
						{/key}
					</div>
				{/if}
			{/each}

			{#if chain.length === 0}
				<p class="mt-3 text-center text-sm text-neutral-500">
					No steps yet — add the first one above.
				</p>
			{/if}
		</section>

		{#if orphanNodes.length > 0}
			<section class="flex flex-col gap-2 rounded-lg border border-ink/12 p-4 dark:border-white/10">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Unconnected steps</h2>
				<p class="text-xs text-neutral-500">
					These steps exist but aren't wired into the chain, so the engine skips them.
				</p>
				<ul class="flex flex-col gap-2">
					{#each orphanNodes as node (node.id)}
						<li
							class="flex items-center justify-between rounded-md border border-ink/12 p-2 dark:border-white/10"
						>
							<span class="text-sm text-ink dark:text-ink-dark"
								>{NODE_TYPE_LABELS[node.node_type]} — {node.name}</span
							>
							<div class="flex items-center gap-2">
								<button
									onclick={() => connectOrphanToEnd(node.id)}
									class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
								>
									Add to end of chain
								</button>
								<button
									onclick={() => handleRemoveNode(node.id)}
									class="text-xs text-neutral-500 hover:text-agent-magenta-600"
								>
									Remove
								</button>
							</div>
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		<section class="flex flex-col gap-3">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Run history</h2>
				<button
					onclick={loadRuns}
					class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
				>
					{runList === null ? 'Load runs' : 'Refresh'}
				</button>
			</div>
			{#if runsError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{runsError}</p>
			{:else if runList === null}
				<p class="text-sm text-neutral-500">
					Not loaded yet — click "Load runs" to see past executions.
				</p>
			{:else if runList.length === 0}
				<p class="text-sm text-neutral-500">
					No runs yet — trigger this workflow from a channel with /{workflow.name} &lt;input&gt;.
				</p>
			{:else}
				<ul class="flex flex-col gap-2">
					{#each runList as run (run.id)}
						<li class="rounded-lg border border-ink/12 dark:border-white/10">
							<button
								type="button"
								onclick={() => toggleRun(run.id)}
								class="flex w-full items-center justify-between px-3 py-2 text-left"
							>
								<span class="flex items-center gap-2 text-sm text-ink dark:text-ink-dark">
									<Icon
										name="chevron"
										class="h-3 w-3 flex-none text-neutral-500 transition-transform duration-150 {expandedRunId ===
										run.id
											? 'rotate-90'
											: ''}"
									/>
									{timeAgo(run.started_at)}
									{#if run.triggered_by === 'workflow'}
										<span class="text-neutral-500">(nested)</span>
									{:else if run.triggered_by === 'schedule'}
										<span class="text-neutral-500">(scheduled)</span>
									{/if}
								</span>
								<span class="rounded-sm px-2 py-0.5 text-xs {statusClass(run.status)}">
									{run.status}
								</span>
							</button>
							{#if expandedRunId === run.id}
								<div class="border-t border-ink/10 px-3 py-2 dark:border-white/10">
									{#if run.error_message}
										<p class="mb-2 text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
											{run.error_message}
										</p>
									{/if}
									{#if nodeRunsError}
										<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
											{nodeRunsError}
										</p>
									{:else if !nodeRunsByRun[run.id]}
										<p class="text-xs text-neutral-500">Loading steps…</p>
									{:else if nodeRunsByRun[run.id].length === 0}
										<p class="text-xs text-neutral-500">No steps recorded for this run.</p>
									{:else}
										<ul class="flex flex-col gap-2">
											{#each nodeRunsByRun[run.id] as nodeRun (nodeRun.id)}
												<li class="flex flex-col gap-1 text-xs">
													<div class="flex items-center gap-2">
														<span class="rounded-sm px-1.5 py-0.5 {statusClass(nodeRun.status)}">
															{nodeRun.status}
														</span>
														<span class="text-neutral-500">
															{nodeList.find((n) => n.id === nodeRun.node_id)?.name ??
																'Deleted step'}
														</span>
													</div>
													{#if nodeRun.output_content}
														<p class="text-neutral-600 dark:text-neutral-400">
															{nodeRun.output_content}
														</p>
													{/if}
													{#if nodeRun.error_message}
														<p class="text-agent-magenta-700 dark:text-agent-magenta-400">
															{nodeRun.error_message}
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
		</section>
	{/if}
</div>
