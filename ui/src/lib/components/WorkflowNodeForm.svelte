<script module lang="ts">
	import type { WorkflowNodeType } from '$lib/api/workflows';

	export interface WorkflowNodeFormValues {
		name: string;
		node_type: WorkflowNodeType;
		agent_id: string | null;
		child_workflow_id: string | null;
		config: Record<string, unknown>;
		retry_max_attempts: number;
		retry_backoff_seconds: number;
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import type { Agent } from '$lib/api/agents';
	import type { Workflow } from '$lib/api/workflows';
	import { NODE_TYPE_LABELS } from '$lib/workflowNodeTypes';

	let {
		agentOptions,
		workflowOptions,
		initial = {
			name: '',
			node_type: 'agent',
			agent_id: null,
			child_workflow_id: null,
			config: {},
			retry_max_attempts: 0,
			retry_backoff_seconds: 5
		},
		lockNodeType = false,
		submitLabel,
		busyLabel,
		busy = false,
		error = null,
		onsubmit,
		oncancel,
		oncreateagent
	}: {
		agentOptions: Agent[];
		// Callers exclude the workflow currently being edited -- a workflow
		// referencing itself is always a cycle, so there's no reason to
		// even offer the choice (the engine's own ancestry guard still
		// catches it, but this avoids the round-trip to find out).
		workflowOptions: Workflow[];
		initial?: WorkflowNodeFormValues;
		lockNodeType?: boolean;
		submitLabel: string;
		busyLabel: string;
		busy?: boolean;
		error?: string | null;
		onsubmit: (values: WorkflowNodeFormValues) => void;
		oncancel?: () => void;
		// #203: fired when the agent picker's "+ Create new agent…" option is
		// chosen. The caller owns the actual creation flow (rendered as a
		// sibling of this form, not nested inside it -- a <form> can't
		// contain another <form>) and pushes the result back in via
		// `setAgentId` below, rather than this component knowing about the
		// agents API itself.
		oncreateagent?: () => void;
	} = $props();

	// A snapshot, taken once -- callers remount this component (keyed block)
	// rather than expecting fields to track `initial` live (see AgentForm.svelte).
	let name = $state(untrack(() => initial.name));
	let nodeType = $state<WorkflowNodeType>(untrack(() => initial.node_type));
	let agentId = $state(untrack(() => initial.agent_id ?? ''));
	let childWorkflowId = $state(untrack(() => initial.child_workflow_id ?? ''));
	let template = $state(untrack(() => (initial.config.template as string | undefined) ?? ''));
	let contains = $state(untrack(() => (initial.config.contains as string | undefined) ?? ''));
	let retryMaxAttempts = $state(untrack(() => initial.retry_max_attempts));
	let retryBackoffSeconds = $state(untrack(() => initial.retry_backoff_seconds));

	// #203: lets the parent select a just-created agent without remounting
	// this form (which would wipe out the name/config the user already typed).
	export function setAgentId(id: string) {
		agentId = id;
	}

	const CREATE_NEW_AGENT_VALUE = '__create_new_agent__';

	function handleAgentSelectChange(event: Event) {
		const value = (event.target as HTMLSelectElement).value;
		if (value === CREATE_NEW_AGENT_VALUE) {
			(event.target as HTMLSelectElement).value = agentId;
			oncreateagent?.();
			return;
		}
		agentId = value;
	}

	function configFor(type: WorkflowNodeType): Record<string, unknown> {
		if (type === 'transform') return template.trim() ? { template } : {};
		if (type === 'conditional') return contains.trim() ? { contains } : {};
		return {};
	}

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim()) return;
		if (nodeType === 'agent' && !agentId) return;
		if (nodeType === 'workflow' && !childWorkflowId) return;
		onsubmit({
			name: name.trim(),
			node_type: nodeType,
			agent_id: nodeType === 'agent' ? agentId : null,
			child_workflow_id: nodeType === 'workflow' ? childWorkflowId : null,
			config: configFor(nodeType),
			retry_max_attempts: retryMaxAttempts,
			retry_backoff_seconds: retryBackoffSeconds
		});
	}
</script>

<form onsubmit={handleSubmit} class="flex flex-col gap-3">
	<div class="flex gap-2">
		<input
			type="text"
			bind:value={name}
			placeholder="Step name"
			class="min-w-0 flex-1 rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
		{#if lockNodeType}
			<span
				class="flex items-center rounded-md border border-ink/15 px-3 py-2 text-sm text-neutral-500 dark:border-white/15"
			>
				{NODE_TYPE_LABELS[nodeType]}
			</span>
		{:else}
			<select
				bind:value={nodeType}
				class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			>
				{#each Object.entries(NODE_TYPE_LABELS) as [value, label] (value)}
					<option {value}>{label}</option>
				{/each}
			</select>
		{/if}
	</div>

	{#if nodeType === 'agent'}
		<select
			value={agentId}
			onchange={handleAgentSelectChange}
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		>
			<option value="" disabled>Select an agent…</option>
			{#each agentOptions as agent (agent.id)}
				<option value={agent.id}>{agent.name}</option>
			{/each}
			<option value={CREATE_NEW_AGENT_VALUE}>+ Create new agent…</option>
		</select>
	{:else if nodeType === 'transform'}
		<textarea
			bind:value={template}
			placeholder="Template — {'{input}'} is replaced with the previous step's output"
			rows="2"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 font-mono text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		></textarea>
	{:else if nodeType === 'conditional'}
		<input
			type="text"
			bind:value={contains}
			placeholder="Stop the run unless the input contains…"
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		/>
	{:else if nodeType === 'summarize'}
		<p class="text-xs text-neutral-500">
			Summarizes the previous step's output with a small model — no configuration needed.
		</p>
	{:else if nodeType === 'merge'}
		<p class="text-xs text-neutral-500">
			Combines every branch that joins here into one output — a JSON array by default, or set a
			template with {'{input0}'}, {'{input1}'}, … placeholders in the step's config.
		</p>
	{:else if nodeType === 'human_input'}
		<p class="text-xs text-neutral-500">
			Pauses the run and waits for a reply in the channel — whatever the human types next becomes
			this step's output, and the rivulet is marked paused until then.
		</p>
	{:else if nodeType === 'workflow'}
		<select
			bind:value={childWorkflowId}
			class="rounded-md border border-ink/15 bg-transparent px-3 py-2 text-sm text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
		>
			<option value="" disabled>Select a workflow…</option>
			{#each workflowOptions as workflow (workflow.id)}
				<option value={workflow.id}>/{workflow.name}</option>
			{/each}
		</select>
		<p class="text-xs text-neutral-500">
			Runs the selected workflow as a nested step; its result becomes this step's output.
		</p>
	{/if}

	<div class="flex gap-2 text-xs text-neutral-600 dark:text-neutral-400">
		<label class="flex items-center gap-1.5">
			Retries
			<input
				type="number"
				min="0"
				max="10"
				bind:value={retryMaxAttempts}
				class="w-14 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
		</label>
		<label class="flex items-center gap-1.5">
			Backoff (s)
			<input
				type="number"
				min="0"
				max="3600"
				bind:value={retryBackoffSeconds}
				class="w-16 rounded-md border border-ink/15 bg-transparent px-2 py-1 text-ink focus:border-agent-cyan-600 focus:outline-none dark:border-white/15 dark:text-ink-dark"
			/>
		</label>
	</div>

	<div class="flex items-center gap-3">
		<button
			type="submit"
			disabled={busy}
			class="self-start rounded-md bg-agent-cyan px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-agent-cyan-600 disabled:opacity-50"
		>
			{busy ? busyLabel : submitLabel}
		</button>
		{#if oncancel}
			<button
				type="button"
				onclick={oncancel}
				class="text-sm text-neutral-500 hover:text-ink dark:hover:text-ink-dark"
			>
				Cancel
			</button>
		{/if}
	</div>
	{#if error}
		<p class="text-sm text-agent-magenta-700 dark:text-agent-magenta-400">{error}</p>
	{/if}
</form>
