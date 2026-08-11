<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import {
		workflows,
		type Workflow,
		type WorkflowNode,
		type WorkflowNodeType,
		type WorkflowConnection,
		type WorkflowRun,
		type WorkflowNodeRun,
		type WorkflowSchedule,
		type WorkflowWebhook,
		type WorkflowWebhookCreated
	} from '$lib/api/workflows';
	import { agents as agentsApi, type Agent } from '$lib/api/agents';
	import type { Connection } from '@xyflow/svelte';
	import { channels as channelsApi, type Channel } from '$lib/api/channels';
	import { timeAgo } from '$lib/format';
	import Icon from '$lib/components/Icon.svelte';
	import WorkflowNodeForm, {
		type WorkflowNodeFormValues
	} from '$lib/components/WorkflowNodeForm.svelte';
	import WorkflowFlowCanvas from '$lib/components/WorkflowFlowCanvas.svelte';
	import { buildFlowGraph } from '$lib/workflowFlowGraph';
	import { NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';
	import { theme } from '$lib/theme.svelte';

	let workflow = $state<Workflow | null>(null);
	let nodeList = $state<WorkflowNode[]>([]);
	let connectionList = $state<WorkflowConnection[]>([]);
	let agentList = $state<Agent[]>([]);
	let workflowList = $state<Workflow[]>([]);
	let channelList = $state<Channel[]>([]);
	let scheduleList = $state<WorkflowSchedule[]>([]);
	let webhookList = $state<WorkflowWebhook[]>([]);
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

	let editingNodeId = $state<string | null>(null);
	let editBusy = $state(false);
	let editError = $state<string | null>(null);
	let deleteBusy = $state(false);

	let addingNode = $state<{ nodeType: WorkflowNodeType; x: number; y: number } | null>(null);
	let addBusy = $state(false);
	let addError = $state<string | null>(null);

	let editingEdgeId = $state<string | null>(null);
	let edgeDeleteBusy = $state(false);
	let edgeDeleteError = $state<string | null>(null);
	let connectError = $state<string | null>(null);
	// #198: seeded from the selected connection's condition_json in
	// startEditEdge -- 'none' means "always follow", the same as a null
	// condition_json (see _validate_condition, api/workflows.py).
	let editingEdgeCondition = $state<{ operator: 'none' | 'contains' | 'not_contains'; value: string }>(
		{ operator: 'none', value: '' }
	);
	let conditionBusy = $state(false);
	let conditionError = $state<string | null>(null);

	let runList = $state<WorkflowRun[] | null>(null);
	let runsError = $state<string | null>(null);
	let expandedRunId = $state<string | null>(null);
	let nodeRunsByRun = $state<Record<string, WorkflowNodeRun[]>>({});
	let nodeRunsError = $state<string | null>(null);

	function subtitleFor(node: WorkflowNode): string | null {
		if (node.node_type === 'agent') {
			return agentList.find((a) => a.id === node.agent_id)?.name ?? 'Deleted agent';
		}
		if (node.node_type === 'transform' && node.config.template) {
			return String(node.config.template);
		}
		if (node.node_type === 'conditional' && node.config.contains) {
			return `stop unless input contains "${node.config.contains}"`;
		}
		if (node.node_type === 'workflow') {
			return `/${workflowList.find((w) => w.id === node.child_workflow_id)?.name ?? 'Deleted workflow'}`;
		}
		return null;
	}

	const flowGraph = $derived(buildFlowGraph(nodeList, connectionList, subtitleFor));

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
				loadedSchedules,
				loadedWebhooks
			] = await Promise.all([
				workflows.get(workflowId),
				workflows.listNodes(workflowId),
				workflows.listConnections(workflowId),
				agentsApi.list(),
				workflows.list(),
				channelsApi.list(),
				workflows.listSchedules(workflowId),
				workflows.listWebhooks(workflowId)
			]);
			workflow = loadedWorkflow;
			nodeList = loadedNodes;
			connectionList = loadedConnections;
			agentList = loadedAgents;
			workflowList = loadedWorkflows;
			channelList = loadedChannels;
			scheduleList = loadedSchedules;
			webhookList = loadedWebhooks;
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

	let remediationBusy = $state(false);
	let remediationError = $state<string | null>(null);

	// #94 layer 2: saved immediately on selection change, same
	// "no separate edit mode" pattern togglePublish uses -- `value` is
	// '' for the "None" option, mapped to null to clear remediation.
	async function updateRemediation(value: string) {
		if (!workflow) return;
		remediationError = null;
		remediationBusy = true;
		try {
			workflow = await workflows.update(workflow.id, {
				on_failure_workflow_id: value || null
			});
		} catch (err) {
			remediationError = err instanceof Error ? err.message : 'Failed to update remediation';
		} finally {
			remediationBusy = false;
		}
	}

	let onCallBusy = $state(false);
	let onCallError = $state<string | null>(null);

	// #94 layer 3: same immediate-save pattern as updateRemediation --
	// independently configurable, not a replacement for it.
	async function updateOnCallAgent(value: string) {
		if (!workflow) return;
		onCallError = null;
		onCallBusy = true;
		try {
			workflow = await workflows.update(workflow.id, {
				on_call_agent_id: value || null
			});
		} catch (err) {
			onCallError = err instanceof Error ? err.message : 'Failed to update on-call agent';
		} finally {
			onCallBusy = false;
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

	// Plain-language schedule builder (#131) — generates the cron expression
	// under the hood; the raw field is still available as "Advanced".
	const WEEKDAY_OPTIONS = [
		{ value: 1, label: 'Monday' },
		{ value: 2, label: 'Tuesday' },
		{ value: 3, label: 'Wednesday' },
		{ value: 4, label: 'Thursday' },
		{ value: 5, label: 'Friday' },
		{ value: 6, label: 'Saturday' },
		{ value: 0, label: 'Sunday' }
	];
	let scheduleAdvanced = $state(false);
	let scheduleFrequencyDraft = $state<'daily' | 'weekly' | 'hourly'>('daily');
	let scheduleTimeDraft = $state('09:00');
	let scheduleWeekdayDraft = $state(1);
	let scheduleMinuteDraft = $state(0);

	function buildCronFromSimple() {
		if (scheduleFrequencyDraft === 'hourly') {
			const minute = Math.min(59, Math.max(0, scheduleMinuteDraft || 0));
			scheduleCronDraft = `${minute} * * * *`;
		} else {
			const [hourStr, minuteStr] = scheduleTimeDraft.split(':');
			const hour = Number(hourStr) || 0;
			const minute = Number(minuteStr) || 0;
			scheduleCronDraft =
				scheduleFrequencyDraft === 'weekly'
					? `${minute} ${hour} * * ${scheduleWeekdayDraft}`
					: `${minute} ${hour} * * *`;
		}
		onCronDraftChange();
	}

	function openAddSchedule() {
		showAddSchedule = true;
		scheduleAdvanced = false;
		scheduleFrequencyDraft = 'daily';
		scheduleTimeDraft = '09:00';
		scheduleWeekdayDraft = 1;
		scheduleMinuteDraft = 0;
		scheduleCronDraft = '';
		scheduleChannelDraft = channelList[0]?.id ?? '';
		scheduleInputDraft = '';
		schedulePreview = null;
		scheduleError = null;
		buildCronFromSimple();
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

	let showAddWebhook = $state(false);
	let webhookChannelDraft = $state('');
	let webhookNameDraft = $state('');
	let webhookBusy = $state(false);
	let webhookError = $state<string | null>(null);
	// Set only right after create/rotate -- the secret is shown exactly
	// once (same UX as an invite link) and cleared the moment the human
	// navigates away from this reveal panel.
	let revealedWebhook = $state<WorkflowWebhookCreated | null>(null);

	function webhookTriggerUrl(webhookId: string): string {
		return `${window.location.origin}/api/v1/webhooks/${webhookId}`;
	}

	function openAddWebhook() {
		showAddWebhook = true;
		webhookChannelDraft = channelList[0]?.id ?? '';
		webhookNameDraft = '';
		webhookError = null;
	}

	function closeAddWebhook() {
		showAddWebhook = false;
	}

	async function handleAddWebhook() {
		if (!workflow || !webhookChannelDraft) return;
		const workflowId = workflow.id;
		webhookError = null;
		webhookBusy = true;
		try {
			const created = await workflows.createWebhook(workflowId, {
				channel_id: webhookChannelDraft,
				name: webhookNameDraft || undefined
			});
			showAddWebhook = false;
			revealedWebhook = created;
			webhookList = await workflows.listWebhooks(workflowId);
		} catch (err) {
			webhookError = err instanceof Error ? err.message : 'Failed to create webhook';
		} finally {
			webhookBusy = false;
		}
	}

	async function toggleWebhookEnabled(webhook: WorkflowWebhook) {
		if (!workflow) return;
		const workflowId = workflow.id;
		webhookError = null;
		try {
			const updated = await workflows.updateWebhook(workflowId, webhook.id, {
				enabled: !webhook.enabled
			});
			webhookList = webhookList.map((w) => (w.id === updated.id ? updated : w));
		} catch (err) {
			webhookError = err instanceof Error ? err.message : 'Failed to update webhook';
		}
	}

	async function rotateWebhookSecret(webhookId: string) {
		if (!workflow) return;
		webhookError = null;
		try {
			const rotated = await workflows.rotateWebhookSecret(workflow.id, webhookId);
			revealedWebhook = rotated;
			webhookList = webhookList.map((w) => (w.id === rotated.id ? rotated : w));
		} catch (err) {
			webhookError = err instanceof Error ? err.message : 'Failed to rotate secret';
		}
	}

	async function removeWebhook(webhookId: string) {
		if (!workflow) return;
		const workflowId = workflow.id;
		webhookError = null;
		try {
			await workflows.removeWebhook(workflowId, webhookId);
			webhookList = webhookList.filter((w) => w.id !== webhookId);
			if (revealedWebhook?.id === webhookId) revealedWebhook = null;
		} catch (err) {
			webhookError = err instanceof Error ? err.message : 'Failed to remove webhook';
		}
	}

	function startEditNode(nodeId: string) {
		editError = null;
		addingNode = null;
		editingEdgeId = null;
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

	async function handleDeleteNode(nodeId: string) {
		if (!workflow) return;
		editError = null;
		deleteBusy = true;
		try {
			await workflows.removeNode(workflow.id, nodeId);
			editingNodeId = null;
			await load(workflow.id);
		} catch (err) {
			editError = err instanceof Error ? err.message : 'Failed to delete step';
		} finally {
			deleteBusy = false;
		}
	}

	// Dropping a palette entry doesn't create the node immediately -- 'agent'
	// and 'workflow' node types have a required agent_id/child_workflow_id
	// the drop itself can't supply, so every type opens the same locked-type
	// form (at the drop position) rather than special-casing the types that
	// happen to need no further input.
	function startAddNode(nodeType: WorkflowNodeType, position: { x: number; y: number }) {
		addError = null;
		editingNodeId = null;
		editingEdgeId = null;
		addingNode = { nodeType, x: position.x, y: position.y };
	}

	async function handleAddNode(values: WorkflowNodeFormValues) {
		if (!workflow || !addingNode) return;
		addError = null;
		addBusy = true;
		try {
			await workflows.createNode(workflow.id, {
				name: values.name,
				node_type: values.node_type,
				agent_id: values.node_type === 'agent' ? values.agent_id : undefined,
				child_workflow_id: values.node_type === 'workflow' ? values.child_workflow_id : undefined,
				config: values.config,
				retry_max_attempts: values.retry_max_attempts,
				retry_backoff_seconds: values.retry_backoff_seconds,
				position_x: addingNode.x,
				position_y: addingNode.y
			});
			addingNode = null;
			await load(workflow.id);
		} catch (err) {
			addError = err instanceof Error ? err.message : 'Failed to add step';
		} finally {
			addBusy = false;
		}
	}

	// Optimistic local update first so the node doesn't snap back to its
	// pre-drag position while the PATCH is in flight -- flowGraph is
	// $derived from nodeList, so this takes effect the moment nodeList
	// changes, no reload needed.
	async function handleNodesMoved(updates: { id: string; positionX: number; positionY: number }[]) {
		if (!workflow) return;
		const workflowId = workflow.id;
		nodeList = nodeList.map((n) => {
			const update = updates.find((u) => u.id === n.id);
			return update ? { ...n, position_x: update.positionX, position_y: update.positionY } : n;
		});
		await Promise.all(
			updates.map((u) =>
				workflows.updateNode(workflowId, u.id, {
					position_x: u.positionX,
					position_y: u.positionY
				})
			)
		);
	}

	function startEditEdge(edgeId: string) {
		edgeDeleteError = null;
		conditionError = null;
		editingNodeId = null;
		addingNode = null;
		editingEdgeId = edgeId;
		const condition = connectionList.find((c) => c.id === edgeId)?.condition_json ?? null;
		if (condition && typeof condition.contains === 'string') {
			editingEdgeCondition = { operator: 'contains', value: condition.contains };
		} else if (condition && typeof condition.not_contains === 'string') {
			editingEdgeCondition = { operator: 'not_contains', value: condition.not_contains };
		} else {
			editingEdgeCondition = { operator: 'none', value: '' };
		}
	}

	async function handleUpdateEdgeCondition(edgeId: string) {
		if (!workflow) return;
		if (editingEdgeCondition.operator !== 'none' && !editingEdgeCondition.value.trim()) return;
		conditionError = null;
		conditionBusy = true;
		try {
			const condition_json =
				editingEdgeCondition.operator === 'none'
					? null
					: { [editingEdgeCondition.operator]: editingEdgeCondition.value.trim() };
			await workflows.updateConnection(workflow.id, edgeId, { condition_json });
			editingEdgeId = null;
			await load(workflow.id);
		} catch (err) {
			conditionError = err instanceof Error ? err.message : 'Failed to update condition';
		} finally {
			conditionBusy = false;
		}
	}

	// Svelte Flow's Handle already optimistically draws the new edge the
	// instant the drag completes (before this even runs) -- reloading
	// unconditionally, success or failure, is what reconciles that
	// optimistic edge with the server's real id (on success) or removes it
	// again (on failure) rather than leaving a phantom edge on screen.
	async function handleConnect(connection: Connection) {
		if (!workflow) return;
		const workflowId = workflow.id;
		connectError = null;
		try {
			await workflows.createConnection(workflowId, {
				from_node_id: connection.source,
				to_node_id: connection.target
			});
		} catch (err) {
			connectError = err instanceof Error ? err.message : 'Failed to create connection';
		} finally {
			await load(workflowId);
		}
	}

	async function handleDeleteConnection(connectionId: string) {
		if (!workflow) return;
		edgeDeleteError = null;
		edgeDeleteBusy = true;
		try {
			await workflows.removeConnection(workflow.id, connectionId);
			editingEdgeId = null;
			await load(workflow.id);
		} catch (err) {
			edgeDeleteError = err instanceof Error ? err.message : 'Failed to delete connection';
		} finally {
			edgeDeleteBusy = false;
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

		<section class="flex flex-col gap-2 rounded-lg border border-ink/12 p-4 dark:border-white/10">
			<h2 class="text-sm font-medium text-ink dark:text-ink-dark">On failure</h2>
			<p class="text-xs text-neutral-500">
				Automatically trigger another workflow when a run of this one fails, with the failure's
				input and error as its input. The original run still shows as failed either way.
			</p>
			<select
				value={workflow.on_failure_workflow_id ?? ''}
				onchange={(e) => updateRemediation(e.currentTarget.value)}
				disabled={remediationBusy}
				class="w-fit rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
			>
				<option value="">None</option>
				{#each workflowList as candidate (candidate.id)}
					<option value={candidate.id}>/{candidate.name}</option>
				{/each}
			</select>
			{#if remediationError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
					{remediationError}
				</p>
			{/if}

			<h3 class="mt-2 text-sm font-medium text-ink dark:text-ink-dark">On-call agent</h3>
			<p class="text-xs text-neutral-500">
				@mention an agent when a run fails, alongside (or instead of) a remediation workflow. It
				only responds if it's on this channel's team. Leave as "Workspace default" to use the
				default configured in Settings.
			</p>
			<select
				value={workflow.on_call_agent_id ?? ''}
				onchange={(e) => updateOnCallAgent(e.currentTarget.value)}
				disabled={onCallBusy}
				class="w-fit rounded-md border border-ink/15 bg-transparent px-2.5 py-1.5 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none disabled:opacity-50 dark:border-white/15 dark:text-ink-dark"
			>
				<option value="">Workspace default</option>
				{#each agentList as agent (agent.id)}
					<option value={agent.id}>{agent.name}</option>
				{/each}
			</select>
			{#if onCallError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
					{onCallError}
				</p>
			{/if}
		</section>

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
					<div class="flex items-center gap-3 text-xs">
						<button
							type="button"
							onclick={() => (scheduleAdvanced = false)}
							class={scheduleAdvanced
								? 'text-neutral-500 hover:text-ink dark:hover:text-ink-dark'
								: 'font-semibold text-ink dark:text-ink-dark'}
						>
							Simple
						</button>
						<button
							type="button"
							onclick={() => (scheduleAdvanced = true)}
							class={scheduleAdvanced
								? 'font-semibold text-ink dark:text-ink-dark'
								: 'text-neutral-500 hover:text-ink dark:hover:text-ink-dark'}
						>
							Advanced (cron)
						</button>
					</div>
					{#if scheduleAdvanced}
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
					{:else}
						<label class="flex flex-col gap-1 text-xs text-neutral-500">
							Frequency
							<select
								bind:value={scheduleFrequencyDraft}
								onchange={buildCronFromSimple}
								class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
							>
								<option value="daily">Every day</option>
								<option value="weekly">Every week</option>
								<option value="hourly">Every hour</option>
							</select>
						</label>
						{#if scheduleFrequencyDraft === 'weekly'}
							<label class="flex flex-col gap-1 text-xs text-neutral-500">
								Day
								<select
									bind:value={scheduleWeekdayDraft}
									onchange={buildCronFromSimple}
									class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
								>
									{#each WEEKDAY_OPTIONS as day (day.value)}
										<option value={day.value}>{day.label}</option>
									{/each}
								</select>
							</label>
						{/if}
						{#if scheduleFrequencyDraft === 'hourly'}
							<label class="flex flex-col gap-1 text-xs text-neutral-500">
								Minute past the hour
								<input
									type="number"
									min="0"
									max="59"
									bind:value={scheduleMinuteDraft}
									oninput={buildCronFromSimple}
									class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
								/>
							</label>
						{:else}
							<label class="flex flex-col gap-1 text-xs text-neutral-500">
								Time
								<input
									type="time"
									bind:value={scheduleTimeDraft}
									onchange={buildCronFromSimple}
									class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
								/>
							</label>
						{/if}
						<p class="font-mono text-xs text-neutral-500">{scheduleCronDraft}</p>
					{/if}
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

		<section class="flex flex-col gap-3 rounded-lg border border-ink/12 p-4 dark:border-white/10">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-medium text-ink dark:text-ink-dark">Webhooks</h2>
				<button
					onclick={openAddWebhook}
					class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
				>
					+ Add webhook
				</button>
			</div>
			{#if !workflow.published}
				<p class="text-xs text-neutral-500">
					Webhooks won't fire until this workflow is published.
				</p>
			{/if}
			<p class="text-xs text-neutral-500">
				An external system can POST to a webhook's URL to trigger this workflow, signed with its
				secret (see the docs for the signing headers). Only reachable from outside this machine if
				you've deliberately exposed it beyond localhost — same caveat as an invite link.
			</p>
			{#if webhookError}
				<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{webhookError}</p>
			{/if}

			{#if revealedWebhook}
				<div
					class="bg-agent-cyan-50 dark:bg-agent-cyan-950/20 flex flex-col gap-2 rounded-md border border-agent-cyan-600/40 p-3 text-xs"
				>
					<p class="font-medium text-ink dark:text-ink-dark">
						Save this secret now — it won't be shown again.
					</p>
					<label class="flex flex-col gap-1 text-neutral-500">
						URL
						<input
							readonly
							value={webhookTriggerUrl(revealedWebhook.id)}
							onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 font-mono text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					</label>
					<label class="flex flex-col gap-1 text-neutral-500">
						Secret
						<input
							readonly
							value={revealedWebhook.secret}
							onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 font-mono text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					</label>
					<button
						onclick={() => (revealedWebhook = null)}
						class="self-start text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
					>
						Done
					</button>
				</div>
			{/if}

			{#if webhookList.length === 0 && !showAddWebhook}
				<p class="text-sm text-neutral-500">No webhooks configured.</p>
			{:else}
				<ul class="flex flex-col gap-2">
					{#each webhookList as webhook (webhook.id)}
						<li
							class="flex flex-col gap-1 rounded-md border border-ink/12 p-2 text-xs dark:border-white/10"
						>
							<div class="flex items-center justify-between">
								<span class="font-mono text-sm text-ink dark:text-ink-dark">
									{webhook.name ?? webhook.id.slice(0, 8)}
								</span>
								<div class="flex items-center gap-2">
									<button
										onclick={() => toggleWebhookEnabled(webhook)}
										class="text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
									>
										{webhook.enabled ? 'Disable' : 'Enable'}
									</button>
									<button
										onclick={() => rotateWebhookSecret(webhook.id)}
										class="text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
									>
										Rotate secret
									</button>
									<button
										onclick={() => removeWebhook(webhook.id)}
										class="text-neutral-500 hover:text-agent-magenta-600"
									>
										Remove
									</button>
								</div>
							</div>
							<p class="text-neutral-500">
								Channel: {channelList.find((c) => c.id === webhook.channel_id)?.name ??
									'Deleted channel'}
							</p>
							<p class="text-neutral-500">
								Last triggered: {webhook.last_triggered_at
									? timeAgo(webhook.last_triggered_at)
									: 'never'}
							</p>
						</li>
					{/each}
				</ul>
			{/if}

			{#if showAddWebhook}
				<div class="flex flex-col gap-2 rounded-md border border-ink/12 p-3 dark:border-white/10">
					<label class="flex flex-col gap-1 text-xs text-neutral-500">
						Name (optional)
						<input
							type="text"
							bind:value={webhookNameDraft}
							placeholder="e.g. GitHub"
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					</label>
					<label class="flex flex-col gap-1 text-xs text-neutral-500">
						Channel
						<select
							bind:value={webhookChannelDraft}
							class="rounded-md border border-ink/15 bg-transparent px-2 py-1 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						>
							{#each channelList as channel (channel.id)}
								<option value={channel.id}>{channel.name}</option>
							{/each}
						</select>
					</label>
					{#if webhookError}
						<p class="text-xs text-agent-magenta-700 dark:text-agent-magenta-400">
							{webhookError}
						</p>
					{/if}
					<div class="flex gap-3">
						<button
							onclick={handleAddWebhook}
							disabled={webhookBusy || !webhookChannelDraft}
							class="self-start rounded-md bg-agent-cyan px-3 py-1.5 text-xs font-semibold text-white hover:bg-agent-cyan-600 disabled:opacity-50"
						>
							{webhookBusy ? 'Adding…' : 'Add webhook'}
						</button>
						<button
							onclick={closeAddWebhook}
							class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
						>
							Cancel
						</button>
					</div>
				</div>
			{/if}
		</section>

		<section
			class="relative w-full overflow-hidden rounded-lg border border-ink/12 dark:border-white/10"
			style="height: 34rem"
		>
			<WorkflowFlowCanvas
				nodes={flowGraph.nodes}
				edges={flowGraph.edges}
				colorMode={theme.preference}
				onnodeclick={startEditNode}
				onnodesmoved={handleNodesMoved}
				onpalettedrop={startAddNode}
				onconnect={handleConnect}
				onedgeclick={startEditEdge}
			/>
		</section>

		{#if flowGraph.nodes.length === 0}
			<p class="text-center text-sm text-neutral-500">
				No steps yet — drag one from the palette above onto the canvas.
			</p>
		{/if}

		{#if connectError}
			<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{connectError}</p>
		{/if}

		{#if editingEdgeId}
			<section
				class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<p class="text-sm text-ink dark:text-ink-dark">Connection selected.</p>
				<form
					onsubmit={(event) => {
						event.preventDefault();
						handleUpdateEdgeCondition(editingEdgeId!);
					}}
					class="mt-3 flex flex-col gap-2"
				>
					<label class="flex flex-col gap-1 text-xs text-neutral-600 dark:text-neutral-400">
						Condition
						<select
							bind:value={editingEdgeCondition.operator}
							class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						>
							<option value="none">Always follow</option>
							<option value="contains">Follow if output contains…</option>
							<option value="not_contains">Follow if output does not contain…</option>
						</select>
					</label>
					{#if editingEdgeCondition.operator !== 'none'}
						<input
							type="text"
							bind:value={editingEdgeCondition.value}
							placeholder="Text to match"
							class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
						/>
					{/if}
					<div class="flex items-center gap-3">
						<button
							type="submit"
							disabled={conditionBusy}
							class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
						>
							{conditionBusy ? 'Saving…' : 'Save condition'}
						</button>
					</div>
					{#if conditionError}
						<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
							{conditionError}
						</p>
					{/if}
				</form>
				{#if edgeDeleteError}
					<p class="mt-2 text-sm text-agent-magenta-700 dark:text-agent-magenta-400">
						{edgeDeleteError}
					</p>
				{/if}
				<div class="mt-3 flex gap-3">
					<button
						onclick={() => handleDeleteConnection(editingEdgeId!)}
						disabled={edgeDeleteBusy}
						class="text-xs text-neutral-500 hover:text-agent-magenta-600 disabled:opacity-50"
					>
						{edgeDeleteBusy ? 'Deleting…' : 'Delete connection'}
					</button>
					<button
						onclick={() => (editingEdgeId = null)}
						class="text-xs text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
					>
						Cancel
					</button>
				</div>
			</section>
		{/if}

		{#if editingNodeId}
			{@const node = nodeList.find((n) => n.id === editingNodeId)}
			{#if node}
				<section
					class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
				>
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
					<button
						onclick={() => handleDeleteNode(node.id)}
						disabled={deleteBusy}
						class="mt-3 text-xs text-neutral-500 hover:text-agent-magenta-600 disabled:opacity-50"
					>
						{deleteBusy ? 'Deleting…' : 'Delete step'}
					</button>
				</section>
			{/if}
		{/if}

		{#if addingNode}
			<section
				class="rounded-lg border border-ink/12 bg-surface p-4 dark:border-white/10 dark:bg-surface-dark"
			>
				<WorkflowNodeForm
					agentOptions={agentList}
					{workflowOptions}
					lockNodeType
					initial={{
						name: NODE_TYPE_LABELS[addingNode.nodeType],
						node_type: addingNode.nodeType,
						agent_id: null,
						child_workflow_id: null,
						config: {},
						retry_max_attempts: 0,
						retry_backoff_seconds: 5
					}}
					submitLabel="Add step"
					busyLabel="Adding…"
					busy={addBusy}
					error={addError}
					onsubmit={handleAddNode}
					oncancel={() => (addingNode = null)}
				/>
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
									{:else if run.triggered_by === 'remediation'}
										<span class="text-neutral-500">(remediation)</span>
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
