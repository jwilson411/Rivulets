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
	import { auth } from '$lib/api/auth.svelte';
	import type { Connection } from '@xyflow/svelte';
	import { channels as channelsApi, type Channel } from '$lib/api/channels';
	import { timeAgo } from '$lib/format';
	import Button from '$lib/ui/Button.svelte';
	import Icon from '$lib/ui/Icon.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import StatusPill from '$lib/ui/StatusPill.svelte';
	import WorkflowNodeForm, {
		type WorkflowNodeFormValues
	} from '$lib/components/WorkflowNodeForm.svelte';
	import WorkflowFlowCanvas from '$lib/components/WorkflowFlowCanvas.svelte';
	import {
		buildFlowGraph,
		buildRunOverlay,
		isLoopEdge,
		LOOP_MAX_NODE_VISITS,
		LOOP_MAX_TOTAL_STEPS
	} from '$lib/workflowFlowGraph';
	import { NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';
	import { theme } from '$lib/theme.svelte';

	// Workflow canvas (06-screens.md → Workflow canvas, mockup 1k): full
	// bleed. Schedules and webhooks live in the inspector's Triggers tab,
	// never above the graph; run history is the Runs tab.

	type InspectorTab = 'step' | 'triggers' | 'runs';
	let inspectorTab = $state<InspectorTab>('step');

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
	let renameBusy = $state(false);

	let publishBusy = $state(false);
	let publishError = $state<string | null>(null);

	let editingNodeId = $state<string | null>(null);
	let editBusy = $state(false);
	let editError = $state<string | null>(null);
	let deleteBusy = $state(false);

	let addingNode = $state<{ nodeType: WorkflowNodeType; x: number; y: number } | null>(null);
	let addBusy = $state(false);
	let addError = $state<string | null>(null);

	// #203: inline "create new agent" from either node panel -- a sibling
	// section, not nested inside WorkflowNodeForm's own <form>. nodeFormRef
	// lets the created agent be pushed back into whichever panel is open
	// without remounting it (which would lose the config already typed in).
	let nodeFormRef: WorkflowNodeForm | undefined = $state();
	let creatingAgentForNode = $state(false);
	let createAgentBusy = $state(false);
	let createAgentError = $state<string | null>(null);
	let newAgentName = $state('');
	let newAgentRole = $state('');
	let newAgentInstructions = $state('');

	let editingEdgeId = $state<string | null>(null);
	let edgeDeleteBusy = $state(false);
	let edgeDeleteError = $state<string | null>(null);
	let connectError = $state<string | null>(null);
	let nodesMoveError = $state<string | null>(null);
	// Chained onto for every drag gesture so overlapping PATCH batches (the
	// user drags node A, then immediately drags node B before A's request
	// resolves) are sent one at a time instead of racing -- otherwise a
	// slower first request finishing after the second could stomp on the
	// second's already-persisted position.
	let nodesMoveQueue: Promise<void> = Promise.resolve();
	// #198: seeded from the selected connection's condition_json in
	// startEditEdge -- 'none' means "always follow", the same as a null
	// condition_json (see _validate_condition, api/workflows.py).
	let editingEdgeCondition = $state<{
		operator: 'none' | 'contains' | 'not_contains';
		value: string;
	}>({ operator: 'none', value: '' });
	let conditionBusy = $state(false);
	let conditionError = $state<string | null>(null);

	let runList = $state<WorkflowRun[] | null>(null);
	let runsError = $state<string | null>(null);
	let expandedRunId = $state<string | null>(null);
	let nodeRunsByRun = $state<Record<string, WorkflowNodeRun[]>>({});
	let nodeRunsError = $state<string | null>(null);
	// #202: the run currently painted onto the canvas, if any -- only ever
	// set once that run's node-runs are already in nodeRunsByRun (see
	// viewRunOnCanvas), so runOverlay below never needs a loading state.
	let overlayRunId = $state<string | null>(null);

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

	// #202: null unless a run is currently overlaid on the canvas -- built
	// from that run's already-loaded node-runs (see viewRunOnCanvas).
	const runOverlay = $derived.by(() => {
		if (!overlayRunId) return null;
		const run = runList?.find((r) => r.id === overlayRunId);
		const nodeRuns = nodeRunsByRun[overlayRunId];
		if (!run || !nodeRuns) return null;
		return buildRunOverlay(nodeRuns, run.status === 'running' || run.status === 'awaiting_human');
	});

	// #200/#201/#202: see buildFlowGraph -- merge-branch highlighting, the
	// nested-workflow drill-in gate, and the run overlay all ride along.
	const flowGraph = $derived(
		buildFlowGraph(
			nodeList,
			connectionList,
			subtitleFor,
			editingNodeId,
			(id) => workflowList.some((w) => w.id === id),
			runOverlay
		)
	);

	// #199: whether the edge currently open in the inspector is a loop edge,
	// so that panel can add the visit-cap explainer only when it's relevant.
	const editingEdgeIsLoop = $derived.by(() => {
		const edge = connectionList.find((c) => c.id === editingEdgeId);
		return edge ? isLoopEdge(connectionList, edge) : false;
	});

	// #315: mirrors api/workflows.py's _require_owner_if_published -- an
	// invite-grant session can freely edit a draft workflow's graph, same
	// as an owner, but once it's published only the owner can rewrite the
	// live graph a schedule/webhook/slash-command can already fire against.
	const canEditGraph = $derived(auth.grant === 'owner' || !workflow?.published);
	// #393 (#356 leftover): a published workflow's name *is* its /{name}
	// slash command. The server 403s that PATCH for invite-grant sessions.
	const canRename = $derived(auth.grant === 'owner' || !workflow?.published);

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
		} catch {
			loadError = "Couldn't load this workflow.";
		}
	}

	$effect(() => {
		load(page.params.id!);
	});

	function startRename() {
		if (!workflow || !canRename) return;
		renameError = null;
		nameDraft = workflow.name;
		descriptionDraft = workflow.description ?? '';
		renaming = true;
	}

	async function saveRename() {
		if (!workflow) return;
		renameError = null;
		renameBusy = true;
		try {
			workflow = await workflows.update(workflow.id, {
				name: nameDraft.trim(),
				description: descriptionDraft.trim() || null
			});
			renaming = false;
		} catch (err) {
			renameError = err instanceof Error ? err.message : "Couldn't rename this workflow.";
		} finally {
			renameBusy = false;
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
		} catch {
			publishError = "Couldn't change the publish state.";
		} finally {
			publishBusy = false;
		}
	}

	let remediationBusy = $state(false);
	let remediationError = $state<string | null>(null);

	// #94 layer 2: saved immediately on selection change -- '' is the
	// "None" option, mapped to null to clear remediation.
	async function updateRemediation(value: string) {
		if (!workflow) return;
		remediationError = null;
		remediationBusy = true;
		try {
			workflow = await workflows.update(workflow.id, {
				on_failure_workflow_id: value || null
			});
		} catch {
			remediationError = "Couldn't save that.";
		} finally {
			remediationBusy = false;
		}
	}

	let onCallBusy = $state(false);
	let onCallError = $state<string | null>(null);

	// #94 layer 3: same immediate-save pattern as updateRemediation.
	async function updateOnCallAgent(value: string) {
		if (!workflow) return;
		onCallError = null;
		onCallBusy = true;
		try {
			workflow = await workflows.update(workflow.id, {
				on_call_agent_id: value || null
			});
		} catch {
			onCallError = "Couldn't save that.";
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
	// under the hood; the raw field is still available under "Advanced".
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
					error: err instanceof Error ? err.message : "Couldn't preview that schedule."
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
			scheduleError = err instanceof Error ? err.message : "Couldn't create that schedule.";
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
		} catch {
			scheduleError = "Couldn't change that schedule.";
		}
	}

	async function removeSchedule(scheduleId: string) {
		if (!workflow) return;
		const workflowId = workflow.id;
		scheduleError = null;
		try {
			await workflows.removeSchedule(workflowId, scheduleId);
			scheduleList = scheduleList.filter((s) => s.id !== scheduleId);
		} catch {
			scheduleError = "Couldn't remove that schedule.";
		}
	}

	let showAddWebhook = $state(false);
	let webhookChannelDraft = $state('');
	let webhookNameDraft = $state('');
	let webhookBusy = $state(false);
	let webhookError = $state<string | null>(null);
	// Set only right after create/rotate -- the secret is shown exactly
	// once (same UX as an invite link) and cleared when dismissed.
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
			webhookError = err instanceof Error ? err.message : "Couldn't create that webhook.";
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
		} catch {
			webhookError = "Couldn't change that webhook.";
		}
	}

	async function rotateWebhookSecret(webhookId: string) {
		if (!workflow) return;
		webhookError = null;
		try {
			const rotated = await workflows.rotateWebhookSecret(workflow.id, webhookId);
			revealedWebhook = rotated;
			webhookList = webhookList.map((w) => (w.id === rotated.id ? rotated : w));
		} catch {
			webhookError = "Couldn't rotate that secret.";
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
		} catch {
			webhookError = "Couldn't remove that webhook.";
		}
	}

	function startEditNode(nodeId: string) {
		editError = null;
		addingNode = null;
		editingEdgeId = null;
		creatingAgentForNode = false;
		editingNodeId = nodeId;
		inspectorTab = 'step';
	}

	async function handleEditNode(nodeId: string, values: WorkflowNodeFormValues) {
		if (!workflow) return;
		if (!canEditGraph) {
			editError = "Only the owner can edit a published workflow's board — unpublish it first.";
			return;
		}
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
			editError = err instanceof Error ? err.message : "Couldn't save that step.";
		} finally {
			editBusy = false;
		}
	}

	async function handleDeleteNode(nodeId: string) {
		if (!workflow) return;
		if (!canEditGraph) {
			editError = "Only the owner can edit a published workflow's board — unpublish it first.";
			return;
		}
		editError = null;
		deleteBusy = true;
		try {
			await workflows.removeNode(workflow.id, nodeId);
			editingNodeId = null;
			await load(workflow.id);
		} catch (err) {
			editError = err instanceof Error ? err.message : "Couldn't delete that step.";
		} finally {
			deleteBusy = false;
		}
	}

	// Dropping a palette entry doesn't create the node immediately -- 'agent'
	// and 'workflow' node types have a required agent_id/child_workflow_id
	// the drop itself can't supply, so every type opens the same locked-type
	// form (at the drop position).
	function startAddNode(nodeType: WorkflowNodeType, position: { x: number; y: number }) {
		if (!canEditGraph) return;
		addError = null;
		editingNodeId = null;
		editingEdgeId = null;
		creatingAgentForNode = false;
		addingNode = { nodeType, x: position.x, y: position.y };
		inspectorTab = 'step';
	}

	async function handleAddNode(values: WorkflowNodeFormValues) {
		if (!workflow || !addingNode || !canEditGraph) return;
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
			addError = err instanceof Error ? err.message : "Couldn't add that step.";
		} finally {
			addBusy = false;
		}
	}

	// #203: create an agent inline from the step panel, without navigating
	// away and losing the in-progress node draft. Pushes the new agent's id
	// back into whichever WorkflowNodeForm is open via nodeFormRef.
	async function handleCreateAgentInline() {
		if (!newAgentName.trim() || !newAgentRole.trim() || !newAgentInstructions.trim()) return;
		createAgentError = null;
		createAgentBusy = true;
		try {
			const created = await agentsApi.create({
				name: newAgentName.trim(),
				description: newAgentRole.trim(),
				instructions: newAgentInstructions.trim(),
				model: 'auto'
			});
			agentList = [...agentList, created];
			nodeFormRef?.setAgentId(created.id);
			creatingAgentForNode = false;
			newAgentName = '';
			newAgentRole = '';
			newAgentInstructions = '';
		} catch (err) {
			createAgentError = err instanceof Error ? err.message : "Couldn't create that agent.";
		} finally {
			createAgentBusy = false;
		}
	}

	// Optimistic local update first so the node doesn't snap back to its
	// pre-drag position while the PATCH is in flight -- flowGraph is
	// $derived from nodeList, so this takes effect immediately.
	async function handleNodesMoved(updates: { id: string; positionX: number; positionY: number }[]) {
		if (!workflow || !canEditGraph) return;
		const workflowId = workflow.id;
		const previousPositions = new Map(
			updates.map((u) => {
				const n = nodeList.find((n) => n.id === u.id);
				return [u.id, { position_x: n?.position_x ?? null, position_y: n?.position_y ?? null }];
			})
		);
		nodeList = nodeList.map((n) => {
			const update = updates.find((u) => u.id === n.id);
			return update ? { ...n, position_x: update.positionX, position_y: update.positionY } : n;
		});
		nodesMoveQueue = nodesMoveQueue.then(async () => {
			nodesMoveError = null;
			try {
				await Promise.all(
					updates.map((u) =>
						workflows.updateNode(workflowId, u.id, {
							position_x: u.positionX,
							position_y: u.positionY
						})
					)
				);
			} catch {
				nodesMoveError = "Couldn't save that step's position.";
				// Revert only the nodes this gesture moved, back to where they
				// were before it -- not a full reload, so an unrelated queued
				// drag gesture keeps its own already-applied position.
				nodeList = nodeList.map((n) => {
					const previous = previousPositions.get(n.id);
					return previous ? { ...n, ...previous } : n;
				});
			}
		});
		await nodesMoveQueue;
	}

	function startEditEdge(edgeId: string) {
		edgeDeleteError = null;
		conditionError = null;
		editingNodeId = null;
		addingNode = null;
		editingEdgeId = edgeId;
		inspectorTab = 'step';
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
		if (!canEditGraph) {
			conditionError = "Only the owner can edit a published workflow's board — unpublish it first.";
			return;
		}
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
			conditionError = err instanceof Error ? err.message : "Couldn't save that condition.";
		} finally {
			conditionBusy = false;
		}
	}

	// Svelte Flow's Handle already optimistically draws the new edge the
	// instant the drag completes -- reloading unconditionally reconciles
	// that optimistic edge with the server's real id (on success) or
	// removes it again (on failure).
	async function handleConnect(connection: Connection) {
		if (!workflow || !canEditGraph) return;
		const workflowId = workflow.id;
		connectError = null;
		try {
			await workflows.createConnection(workflowId, {
				from_node_id: connection.source,
				to_node_id: connection.target
			});
		} catch {
			connectError = "Couldn't connect those steps.";
		} finally {
			await load(workflowId);
		}
	}

	async function handleDeleteConnection(connectionId: string) {
		if (!workflow) return;
		if (!canEditGraph) {
			edgeDeleteError =
				"Only the owner can edit a published workflow's board — unpublish it first.";
			return;
		}
		edgeDeleteError = null;
		edgeDeleteBusy = true;
		try {
			await workflows.removeConnection(workflow.id, connectionId);
			editingEdgeId = null;
			await load(workflow.id);
		} catch {
			edgeDeleteError = "Couldn't delete that connection.";
		} finally {
			edgeDeleteBusy = false;
		}
	}

	async function loadRuns() {
		if (!workflow) return;
		runsError = null;
		try {
			runList = await workflows.listRuns(workflow.id);
		} catch {
			runsError = "Couldn't load run history.";
		}
	}

	function openRunsTab() {
		inspectorTab = 'runs';
		if (runList === null) loadRuns();
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
		} catch {
			nodeRunsError = "Couldn't load this run's steps.";
		}
	}

	// #202: toggles overlaying `runId`'s node statuses onto the canvas.
	function viewRunOnCanvas(runId: string) {
		overlayRunId = overlayRunId === runId ? null : runId;
	}

	function statusTone(status: string): 'accent' | 'danger' | 'warn' | 'neutral' {
		if (status === 'completed') return 'accent';
		if (status === 'failed') return 'danger';
		if (status === 'awaiting_human' || status === 'running') return 'warn';
		return 'neutral';
	}

	const inputClass =
		'h-12 rounded-lg border border-line bg-surface px-4 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark';

	function tabClass(active: boolean): string {
		return active
			? 'bg-ink font-semibold text-paper dark:bg-ink-dark dark:text-paper-dark'
			: 'font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark';
	}
</script>

<div class="flex h-full flex-col">
	{#if loadError}
		<div class="p-8">
			<p class="text-[15px] text-danger">{loadError}</p>
		</div>
	{:else if !workflow}
		<div class="p-8">
			<div class="breath h-4 w-1/3 rounded-full bg-line dark:bg-line-dark"></div>
		</div>
	{:else}
		<header
			class="flex flex-wrap items-center gap-4 border-b border-line bg-surface px-5 py-4 md:px-8 dark:border-line-dark dark:bg-surface-dark"
		>
			<a
				href={resolve('/workflows')}
				class="flex items-center gap-2 text-[15px] font-semibold text-accent hover:text-accent-deep dark:text-accent-dark"
			>
				<Icon name="back" class="h-4 w-4" />
				Workflows
			</a>
			<span class="font-mono text-base font-medium text-ink dark:text-ink-dark">
				/{workflow.name}
			</span>
			<StatusPill tone={workflow.published ? 'accent' : 'neutral'}>
				{workflow.published ? 'Published' : 'Draft'}
			</StatusPill>
			<span class="ml-auto hidden text-sm text-muted lg:block dark:text-muted-dark">
				{workflow.published
					? `Anyone can run this with /${workflow.name} in a channel.`
					: `Publish to run it with /${workflow.name} in a channel.`}
			</span>
			{#if canRename}
				<button
					type="button"
					onclick={startRename}
					class="text-sm font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
				>
					Rename
				</button>
			{/if}
			{#if auth.grant === 'owner'}
				<Button
					variant={workflow.published ? 'secondary' : 'primary'}
					onclick={togglePublish}
					disabled={publishBusy}
				>
					{publishBusy ? '…' : workflow.published ? 'Unpublish' : 'Publish'}
				</Button>
			{/if}
			{#if publishError}
				<p class="w-full text-sm text-danger">{publishError}</p>
			{/if}
		</header>

		<div class="flex min-h-0 flex-1">
			<div class="relative flex min-w-0 flex-1 flex-col">
				<WorkflowFlowCanvas
					nodes={flowGraph.nodes}
					edges={flowGraph.edges}
					colorMode={theme.preference}
					onnodeclick={startEditNode}
					onnodesmoved={handleNodesMoved}
					onpalettedrop={startAddNode}
					onconnect={handleConnect}
					onedgeclick={startEditEdge}
					readOnly={!canEditGraph}
				/>

				{#if flowGraph.nodes.length === 0}
					<p
						class="pointer-events-none absolute inset-x-0 top-1/2 text-center text-base text-muted dark:text-muted-dark"
					>
						Drag a step onto the board.
					</p>
				{/if}

				{#if overlayRunId}
					{@const overlaidRun = runList?.find((r) => r.id === overlayRunId)}
					{#if overlaidRun}
						<div
							data-testid="workflow-run-overlay-banner"
							class="absolute inset-x-4 top-16 flex items-center gap-3 rounded-xl border px-4 py-2.5 text-sm shadow-pop {overlaidRun.status ===
							'awaiting_human'
								? 'border-warn-line bg-warn-soft text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark'
								: 'border-accent bg-accent-soft text-ink dark:border-accent-dark dark:bg-accent-soft-dark dark:text-ink-dark'}"
						>
							<span class="min-w-0 truncate">
								{#if overlaidRun.status === 'awaiting_human'}
									Waiting on a person at
									<strong>
										{nodeList.find((n) => n.id === overlaidRun.current_node_id)?.name ?? 'a step'}
									</strong>
									— paused {timeAgo(overlaidRun.started_at)}.
								{:else}
									Showing the path taken by the run from {timeAgo(overlaidRun.started_at)}.
								{/if}
							</span>
							<button
								type="button"
								data-testid="workflow-run-overlay-clear"
								onclick={() => (overlayRunId = null)}
								class="ml-auto flex-none font-semibold hover:underline"
							>
								Clear
							</button>
						</div>
					{/if}
				{/if}

				{#if connectError || nodesMoveError}
					<p
						class="absolute inset-x-4 bottom-4 rounded-xl border border-danger-line bg-danger-soft px-4 py-2.5 text-sm text-danger-ink dark:border-danger-line-dark dark:bg-danger-soft-dark dark:text-danger-ink-dark"
					>
						{connectError ?? nodesMoveError}
					</p>
				{/if}
			</div>

			<aside
				class="hidden w-[320px] flex-none flex-col border-l border-line bg-surface md:flex dark:border-line-dark dark:bg-surface-dark"
			>
				<div class="flex gap-1.5 px-5 pt-5">
					<button
						type="button"
						onclick={() => (inspectorTab = 'step')}
						class="flex h-9 items-center rounded-md px-3.5 text-sm {tabClass(
							inspectorTab === 'step'
						)}"
					>
						Step
					</button>
					<button
						type="button"
						onclick={() => (inspectorTab = 'triggers')}
						class="flex h-9 items-center rounded-md px-3.5 text-sm {tabClass(
							inspectorTab === 'triggers'
						)}"
					>
						Triggers
					</button>
					<button
						type="button"
						onclick={openRunsTab}
						class="flex h-9 items-center rounded-md px-3.5 text-sm {tabClass(
							inspectorTab === 'runs'
						)}"
					>
						Runs
					</button>
				</div>

				<div class="flex-1 overflow-y-auto p-5">
					{#if inspectorTab === 'step'}
						{#if addingNode}
							<WorkflowNodeForm
								bind:this={nodeFormRef}
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
								oncreateagent={() => {
									creatingAgentForNode = true;
									createAgentError = null;
								}}
							/>
						{:else if editingNodeId}
							{@const node = nodeList.find((n) => n.id === editingNodeId)}
							{#if node}
								{#if node.node_type === 'merge'}
									{@const incomingCount = connectionList.filter(
										(c) => c.to_node_id === node.id && c.from_node_id
									).length}
									<p
										class="mb-4 text-[13px] leading-normal text-muted dark:text-muted-dark"
										data-testid="workflow-merge-notice"
									>
										{#if flowGraph.mergeFanOut}
											{@const ancestorName =
												nodeList.find((n) => n.id === flowGraph.mergeFanOut?.ancestorId)?.name ??
												'an earlier step'}
											This step joins {flowGraph.mergeFanOut.branchCount} branches that diverged at
											<strong>{ancestorName}</strong>, highlighted on the board.
										{:else if incomingCount < 2}
											Connect at least two incoming steps to this merge to see where its branches
											diverge.
										{:else}
											This step joins {incomingCount} incoming connections, but they don't share a common
											branch point.
										{/if}
									</p>
								{/if}
								{#key node.id}
									<WorkflowNodeForm
										bind:this={nodeFormRef}
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
										submitLabel="Save"
										busyLabel="Saving…"
										busy={editBusy}
										error={editError}
										onsubmit={(values) => handleEditNode(node.id, values)}
										oncancel={() => (editingNodeId = null)}
										oncreateagent={() => {
											creatingAgentForNode = true;
											createAgentError = null;
										}}
									/>
								{/key}
								<button
									type="button"
									onclick={() => handleDeleteNode(node.id)}
									disabled={deleteBusy}
									class="mt-4 text-sm font-medium text-danger hover:underline disabled:opacity-50"
								>
									{deleteBusy ? 'Deleting…' : 'Delete step'}
								</button>
							{/if}
						{:else if editingEdgeId}
							<p class="mb-3 text-[15px] font-semibold text-ink dark:text-ink-dark">
								Connection selected
							</p>
							{#if editingEdgeIsLoop}
								<p
									class="mb-3 text-[13px] leading-normal text-muted dark:text-muted-dark"
									data-testid="workflow-loop-edge-notice"
								>
									This connection loops back to an earlier step. A run stops after
									{LOOP_MAX_NODE_VISITS} visits to the same step or {LOOP_MAX_TOTAL_STEPS} total steps.
								</p>
							{/if}
							<form
								onsubmit={(event) => {
									event.preventDefault();
									handleUpdateEdgeCondition(editingEdgeId!);
								}}
								class="flex flex-col gap-3"
							>
								<label
									class="flex flex-col gap-2 text-sm font-semibold text-ink dark:text-ink-dark"
								>
									When to follow it
									<select bind:value={editingEdgeCondition.operator} class={inputClass}>
										<option value="none">Always</option>
										<option value="contains">If the output contains…</option>
										<option value="not_contains">Unless the output contains…</option>
									</select>
								</label>
								{#if editingEdgeCondition.operator !== 'none'}
									<input
										type="text"
										bind:value={editingEdgeCondition.value}
										placeholder="Text to match"
										aria-label="Text to match"
										class={inputClass}
									/>
								{/if}
								<Button type="submit" size="md" class="self-start" disabled={conditionBusy}>
									{conditionBusy ? 'Saving…' : 'Save'}
								</Button>
								{#if conditionError}
									<p class="text-sm text-danger">{conditionError}</p>
								{/if}
							</form>
							{#if edgeDeleteError}
								<p class="mt-2 text-sm text-danger">{edgeDeleteError}</p>
							{/if}
							<div class="mt-4 flex gap-4">
								<button
									type="button"
									onclick={() => handleDeleteConnection(editingEdgeId!)}
									disabled={edgeDeleteBusy}
									class="text-sm font-medium text-danger hover:underline disabled:opacity-50"
								>
									{edgeDeleteBusy ? 'Deleting…' : 'Delete connection'}
								</button>
								<button
									type="button"
									onclick={() => (editingEdgeId = null)}
									class="text-sm font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
								>
									Cancel
								</button>
							</div>
						{:else}
							<p class="text-[15px] leading-normal text-muted dark:text-muted-dark">
								Select a step on the board, or drag a new one from the palette.
							</p>
							{#if flowGraph.hasLoop}
								<p
									class="mt-4 text-[13px] leading-normal text-muted dark:text-muted-dark"
									data-testid="workflow-loop-notice"
								>
									This workflow loops back to an earlier step (dotted "↻" edge). A run stops after
									{LOOP_MAX_NODE_VISITS} visits to the same step or {LOOP_MAX_TOTAL_STEPS} total steps.
								</p>
							{/if}
						{/if}

						{#if creatingAgentForNode}
							<div
								class="mt-5 flex flex-col gap-3 rounded-xl border border-dashed border-line p-4 dark:border-line-dark"
							>
								<p class="text-sm font-semibold text-ink dark:text-ink-dark">New agent</p>
								<input
									type="text"
									bind:value={newAgentName}
									placeholder="Name"
									aria-label="Agent name"
									class={inputClass}
								/>
								<input
									type="text"
									bind:value={newAgentRole}
									placeholder="What this agent does"
									aria-label="What this agent does"
									class={inputClass}
								/>
								<textarea
									rows="3"
									bind:value={newAgentInstructions}
									placeholder="How it should behave"
									aria-label="How it should behave"
									class="rounded-lg border border-line bg-surface px-4 py-3 text-base text-ink focus:border-accent focus:outline-none dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark dark:focus:border-accent-dark"
								></textarea>
								{#if createAgentError}
									<p class="text-sm text-danger">{createAgentError}</p>
								{/if}
								<div class="flex gap-3">
									<Button
										size="md"
										disabled={createAgentBusy ||
											!newAgentName.trim() ||
											!newAgentRole.trim() ||
											!newAgentInstructions.trim()}
										onclick={handleCreateAgentInline}
									>
										{createAgentBusy ? 'Creating…' : 'Create agent'}
									</Button>
									<button
										type="button"
										onclick={() => (creatingAgentForNode = false)}
										class="text-sm font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
									>
										Cancel
									</button>
								</div>
							</div>
						{/if}
					{:else if inspectorTab === 'triggers'}
						<div class="flex flex-col gap-7">
							{#if !workflow.published}
								<p
									class="rounded-xl border border-warn-line bg-warn-soft px-4 py-3 text-[13px] leading-normal text-warn-ink dark:border-warn-line-dark dark:bg-warn-soft-dark dark:text-warn-ink-dark"
								>
									Nothing here fires until this workflow is published.
								</p>
							{/if}

							<section class="flex flex-col gap-3">
								<div class="flex items-center justify-between">
									<h3 class="text-sm font-semibold text-ink dark:text-ink-dark">Schedules</h3>
									{#if auth.grant === 'owner'}
										<button
											type="button"
											onclick={openAddSchedule}
											class="text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
										>
											Add
										</button>
									{/if}
								</div>
								{#if scheduleError}
									<p class="text-sm text-danger">{scheduleError}</p>
								{/if}
								{#if scheduleList.length === 0 && !showAddSchedule}
									<p class="text-sm text-muted dark:text-muted-dark">No schedules.</p>
								{/if}
								{#each scheduleList as schedule (schedule.id)}
									<div
										class="flex flex-col gap-1 rounded-xl border border-line p-3.5 text-[13px] dark:border-line-dark"
									>
										<div class="flex items-center justify-between gap-2">
											<span class="font-mono text-sm text-ink dark:text-ink-dark">
												{scheduleTiming(schedule)}
											</span>
											{#if auth.grant === 'owner'}
												<span class="flex flex-none gap-2.5">
													{#if schedule.enabled || !isSpentOneOff(schedule)}
														<button
															type="button"
															onclick={() => toggleScheduleEnabled(schedule)}
															class="font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
														>
															{schedule.enabled ? 'Turn off' : 'Turn on'}
														</button>
													{/if}
													<button
														type="button"
														onclick={() => removeSchedule(schedule.id)}
														class="font-medium text-danger hover:underline"
													>
														Remove
													</button>
												</span>
											{/if}
										</div>
										{#if isPendingAgentApproval(schedule)}
											<p class="text-warn">Created by an agent — waiting on your approval.</p>
										{/if}
										<p class="text-muted dark:text-muted-dark">
											In #{channelList.find((c) => c.id === schedule.channel_id)?.name ??
												'a deleted channel'}
											{#if schedule.enabled}
												· next {new Date(schedule.next_fire_at).toLocaleString()}
											{/if}
										</p>
										{#if !schedule.enabled && schedule.consecutive_failures >= 5}
											<p class="text-danger">
												Turned off after {schedule.consecutive_failures} failures in a row
											</p>
										{:else if schedule.consecutive_failures > 0}
											<p class="text-warn">
												{schedule.consecutive_failures} failure{schedule.consecutive_failures === 1
													? ''
													: 's'} in a row
											</p>
										{/if}
									</div>
								{/each}

								{#if showAddSchedule}
									<div
										class="flex flex-col gap-3 rounded-xl border border-line p-3.5 dark:border-line-dark"
									>
										{#if !scheduleAdvanced}
											<label
												class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
											>
												How often
												<select
													bind:value={scheduleFrequencyDraft}
													onchange={buildCronFromSimple}
													class={inputClass}
												>
													<option value="daily">Every day</option>
													<option value="weekly">Every week</option>
													<option value="hourly">Every hour</option>
												</select>
											</label>
											{#if scheduleFrequencyDraft === 'weekly'}
												<label
													class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
												>
													Day
													<select
														bind:value={scheduleWeekdayDraft}
														onchange={buildCronFromSimple}
														class={inputClass}
													>
														{#each WEEKDAY_OPTIONS as day (day.value)}
															<option value={day.value}>{day.label}</option>
														{/each}
													</select>
												</label>
											{/if}
											{#if scheduleFrequencyDraft === 'hourly'}
												<label
													class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
												>
													Minute past the hour
													<input
														type="number"
														min="0"
														max="59"
														bind:value={scheduleMinuteDraft}
														oninput={buildCronFromSimple}
														class={inputClass}
													/>
												</label>
											{:else}
												<label
													class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
												>
													Time
													<input
														type="time"
														bind:value={scheduleTimeDraft}
														onchange={buildCronFromSimple}
														class={inputClass}
													/>
												</label>
											{/if}
										{:else}
											<label
												class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
											>
												Cron expression
												<input
													type="text"
													bind:value={scheduleCronDraft}
													oninput={onCronDraftChange}
													placeholder="0 9 * * *"
													class="{inputClass} font-mono text-sm"
												/>
											</label>
										{/if}
										<button
											type="button"
											onclick={() => (scheduleAdvanced = !scheduleAdvanced)}
											class="self-start text-[13px] font-semibold text-accent hover:underline dark:text-accent-dark"
										>
											{scheduleAdvanced ? 'Plain language instead' : 'Advanced (cron)'}
										</button>
										{#if schedulePreview}
											{#if schedulePreview.error}
												<p class="text-[13px] text-danger">{schedulePreview.error}</p>
											{:else if schedulePreview.next_fire_at}
												<p class="text-[13px] text-muted dark:text-muted-dark">
													Next run: {new Date(schedulePreview.next_fire_at).toLocaleString()}
												</p>
											{/if}
										{/if}
										<label
											class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
										>
											Channel
											<select bind:value={scheduleChannelDraft} class={inputClass}>
												{#each channelList as channel (channel.id)}
													<option value={channel.id}>#{channel.name}</option>
												{/each}
											</select>
										</label>
										<label
											class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
										>
											Input
											<input
												type="text"
												bind:value={scheduleInputDraft}
												placeholder="Passed to the first step"
												class={inputClass}
											/>
										</label>
										<div class="flex gap-3">
											<Button
												size="md"
												onclick={handleAddSchedule}
												disabled={scheduleBusy ||
													!scheduleCronDraft.trim() ||
													!scheduleChannelDraft}
											>
												{scheduleBusy ? 'Adding…' : 'Add schedule'}
											</Button>
											<button
												type="button"
												onclick={closeAddSchedule}
												class="text-sm font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
											>
												Cancel
											</button>
										</div>
									</div>
								{/if}
							</section>

							<section class="flex flex-col gap-3">
								<div class="flex items-center justify-between">
									<h3 class="text-sm font-semibold text-ink dark:text-ink-dark">Webhooks</h3>
									<button
										type="button"
										onclick={openAddWebhook}
										class="text-sm font-semibold text-accent hover:underline dark:text-accent-dark"
									>
										Add
									</button>
								</div>
								<p class="text-[13px] leading-normal text-muted dark:text-muted-dark">
									An outside system can POST to a webhook's address to run this workflow, signed
									with its secret. Only reachable beyond this machine if you've deliberately opened
									it up.
								</p>
								{#if webhookError}
									<p class="text-sm text-danger">{webhookError}</p>
								{/if}

								{#if revealedWebhook}
									<div
										class="flex flex-col gap-2 rounded-xl border border-accent bg-accent-soft p-3.5 text-[13px] dark:border-accent-dark dark:bg-accent-soft-dark"
									>
										<p class="font-semibold text-ink dark:text-ink-dark">
											Save this now — it won't be shown again.
										</p>
										<input
											readonly
											aria-label="Webhook address"
											value={webhookTriggerUrl(revealedWebhook.id)}
											onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
											class="h-10 rounded-lg border border-line bg-surface px-3 font-mono text-xs text-ink dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
										/>
										<input
											readonly
											aria-label="Webhook secret"
											value={revealedWebhook.secret}
											onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
											class="h-10 rounded-lg border border-line bg-surface px-3 font-mono text-xs text-ink dark:border-line-dark dark:bg-surface-dark dark:text-ink-dark"
										/>
										<button
											type="button"
											onclick={() => (revealedWebhook = null)}
											class="self-start font-semibold text-accent hover:underline dark:text-accent-dark"
										>
											Done
										</button>
									</div>
								{/if}

								{#if webhookList.length === 0 && !showAddWebhook}
									<p class="text-sm text-muted dark:text-muted-dark">No webhooks.</p>
								{/if}
								{#each webhookList as webhook (webhook.id)}
									<div
										class="flex flex-col gap-1 rounded-xl border border-line p-3.5 text-[13px] dark:border-line-dark"
									>
										<div class="flex items-center justify-between gap-2">
											<span class="font-mono text-sm text-ink dark:text-ink-dark">
												{webhook.name ?? webhook.id.slice(0, 8)}
											</span>
											<span class="flex flex-none gap-2.5">
												{#if auth.grant === 'owner'}
													<button
														type="button"
														onclick={() => toggleWebhookEnabled(webhook)}
														class="font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
													>
														{webhook.enabled ? 'Turn off' : 'Turn on'}
													</button>
												{/if}
												<button
													type="button"
													onclick={() => rotateWebhookSecret(webhook.id)}
													class="font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
												>
													New secret
												</button>
												<button
													type="button"
													onclick={() => removeWebhook(webhook.id)}
													class="font-medium text-danger hover:underline"
												>
													Remove
												</button>
											</span>
										</div>
										<p class="text-muted dark:text-muted-dark">
											In #{channelList.find((c) => c.id === webhook.channel_id)?.name ??
												'a deleted channel'} · last fired {webhook.last_triggered_at
												? timeAgo(webhook.last_triggered_at)
												: 'never'}
										</p>
									</div>
								{/each}

								{#if showAddWebhook}
									<div
										class="flex flex-col gap-3 rounded-xl border border-line p-3.5 dark:border-line-dark"
									>
										<label
											class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
										>
											Name (optional)
											<input
												type="text"
												bind:value={webhookNameDraft}
												placeholder="e.g. GitHub"
												class={inputClass}
											/>
										</label>
										<label
											class="flex flex-col gap-1.5 text-sm font-semibold text-ink dark:text-ink-dark"
										>
											Channel
											<select bind:value={webhookChannelDraft} class={inputClass}>
												{#each channelList as channel (channel.id)}
													<option value={channel.id}>#{channel.name}</option>
												{/each}
											</select>
										</label>
										<div class="flex gap-3">
											<Button
												size="md"
												onclick={handleAddWebhook}
												disabled={webhookBusy || !webhookChannelDraft}
											>
												{webhookBusy ? 'Adding…' : 'Add webhook'}
											</Button>
											<button
												type="button"
												onclick={() => (showAddWebhook = false)}
												class="text-sm font-medium text-muted hover:text-ink dark:text-muted-dark dark:hover:text-ink-dark"
											>
												Cancel
											</button>
										</div>
									</div>
								{/if}
							</section>

							<section class="flex flex-col gap-3">
								<h3 class="text-sm font-semibold text-ink dark:text-ink-dark">If a run fails</h3>
								<label class="flex flex-col gap-1.5 text-[13px] text-muted dark:text-muted-dark">
									Run another workflow with the failure as its input
									<select
										value={workflow.on_failure_workflow_id ?? ''}
										onchange={(e) => updateRemediation(e.currentTarget.value)}
										disabled={remediationBusy || auth.grant !== 'owner'}
										class="{inputClass} disabled:opacity-50"
									>
										<option value="">None</option>
										{#each workflowOptions as candidate (candidate.id)}
											<option value={candidate.id}>/{candidate.name}</option>
										{/each}
									</select>
								</label>
								{#if remediationError}
									<p class="text-sm text-danger">{remediationError}</p>
								{/if}
								<label class="flex flex-col gap-1.5 text-[13px] text-muted dark:text-muted-dark">
									Bring in an agent
									<select
										value={workflow.on_call_agent_id ?? ''}
										onchange={(e) => updateOnCallAgent(e.currentTarget.value)}
										disabled={onCallBusy || auth.grant !== 'owner'}
										class="{inputClass} disabled:opacity-50"
									>
										<option value="">Workspace default</option>
										{#each agentList as agent (agent.id)}
											<option value={agent.id}>{agent.name}</option>
										{/each}
									</select>
								</label>
								{#if onCallError}
									<p class="text-sm text-danger">{onCallError}</p>
								{/if}
							</section>
						</div>
					{:else if inspectorTab === 'runs'}
						{#if runsError}
							<p class="text-sm text-danger">{runsError}</p>
						{:else if runList === null}
							<div class="breath h-3 w-1/2 rounded-full bg-line dark:bg-line-dark"></div>
						{:else if runList.length === 0}
							<p class="text-sm leading-normal text-muted dark:text-muted-dark">
								Nothing has run yet. Trigger it from a channel with
								<code class="font-mono">/{workflow.name}</code>.
							</p>
						{:else}
							<div class="flex flex-col gap-2">
								{#each runList as run (run.id)}
									<div class="rounded-xl border border-line dark:border-line-dark">
										<button
											type="button"
											onclick={() => toggleRun(run.id)}
											class="flex h-11 w-full items-center gap-2 px-3 text-left"
										>
											<Icon
												name="chevron-right"
												class="h-3.5 w-3.5 flex-none text-muted transition-transform duration-150 dark:text-muted-dark {expandedRunId ===
												run.id
													? 'rotate-90'
													: ''}"
											/>
											<span class="min-w-0 truncate text-[13px] text-ink dark:text-ink-dark">
												{timeAgo(run.started_at)}
												{#if run.triggered_by === 'workflow'}
													<span class="text-muted dark:text-muted-dark">(nested)</span>
												{:else if run.triggered_by === 'schedule'}
													<span class="text-muted dark:text-muted-dark">(scheduled)</span>
												{:else if run.triggered_by === 'remediation'}
													<span class="text-muted dark:text-muted-dark">(auto)</span>
												{/if}
											</span>
											<StatusPill tone={statusTone(run.status)} class="ml-auto h-5 text-xs">
												{run.status === 'awaiting_human' ? 'waiting on a person' : run.status}
											</StatusPill>
										</button>
										{#if expandedRunId === run.id}
											<div class="border-t border-line px-3 py-2.5 dark:border-line-dark">
												{#if run.error_message}
													<p class="mb-2 text-[13px] text-danger">{run.error_message}</p>
												{/if}
												{#if nodeRunsError}
													<p class="text-[13px] text-danger">{nodeRunsError}</p>
												{:else if !nodeRunsByRun[run.id]}
													<div
														class="breath h-2.5 w-1/2 rounded-full bg-line dark:bg-line-dark"
													></div>
												{:else}
													<button
														type="button"
														data-testid={`workflow-run-${run.id}-view-on-canvas`}
														onclick={() => viewRunOnCanvas(run.id)}
														class="mb-2 text-[13px] font-semibold text-accent hover:underline dark:text-accent-dark"
													>
														{overlayRunId === run.id ? 'Hide from board' : 'Show path on board'}
													</button>
													{#if nodeRunsByRun[run.id].length === 0}
														<p class="text-[13px] text-muted dark:text-muted-dark">
															No steps recorded for this run.
														</p>
													{:else}
														<div class="flex flex-col gap-2">
															{#each nodeRunsByRun[run.id] as nodeRun (nodeRun.id)}
																<div class="flex flex-col gap-0.5 text-[13px]">
																	<div class="flex items-center gap-2">
																		<span
																			class="h-2 w-2 flex-none rounded-full {nodeRun.status ===
																			'succeeded'
																				? 'bg-accent dark:bg-accent-dark'
																				: nodeRun.status === 'failed'
																					? 'bg-danger'
																					: nodeRun.status === 'awaiting_human' ||
																						  nodeRun.status === 'running'
																						? 'bg-warn'
																						: 'bg-line dark:bg-line-dark'}"
																		></span>
																		<span class="font-medium text-ink dark:text-ink-dark">
																			{nodeList.find((n) => n.id === nodeRun.node_id)?.name ??
																				'Deleted step'}
																		</span>
																	</div>
																	{#if nodeRun.output_content}
																		<p class="pl-4 text-muted dark:text-muted-dark">
																			{nodeRun.output_content}
																		</p>
																	{/if}
																	{#if nodeRun.error_message}
																		<p class="pl-4 text-danger">{nodeRun.error_message}</p>
																	{/if}
																</div>
															{/each}
														</div>
													{/if}
												{/if}
											</div>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					{/if}
				</div>
			</aside>
		</div>
	{/if}
</div>

{#if renaming && workflow}
	<Sheet title="Rename workflow" onClose={() => (renaming = false)} width={480}>
		<div class="flex flex-col gap-4">
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="wf-rename-name">
					Name
				</label>
				<input
					id="wf-rename-name"
					type="text"
					bind:value={nameDraft}
					class="{inputClass} font-mono text-sm"
				/>
				<p class="text-[13px] text-muted dark:text-muted-dark">
					This is also the command: /{nameDraft.trim() || workflow.name}
				</p>
			</div>
			<div class="flex flex-col gap-2">
				<label class="text-sm font-semibold text-ink dark:text-ink-dark" for="wf-rename-desc">
					What it does
				</label>
				<input id="wf-rename-desc" type="text" bind:value={descriptionDraft} class={inputClass} />
			</div>
			{#if renameError}
				<p class="text-sm text-danger">{renameError}</p>
			{/if}
		</div>
		{#snippet footer()}
			<Button variant="secondary" onclick={() => (renaming = false)}>Cancel</Button>
			<Button onclick={saveRename} disabled={renameBusy || !nameDraft.trim()}>
				{renameBusy ? 'Saving…' : 'Save'}
			</Button>
		{/snippet}
	</Sheet>
{/if}
