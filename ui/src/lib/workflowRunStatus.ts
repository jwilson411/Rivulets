import type { NodeRunStatus } from './workflowFlowGraph';

export const NODE_RUN_STATUS_LABELS: Record<NodeRunStatus, string> = {
	pending: 'Pending',
	running: 'Running',
	succeeded: 'Succeeded',
	failed: 'Failed',
	skipped: 'Skipped',
	awaiting_human: 'Awaiting human input'
};

// Names from Icon.svelte's fixed set -- 'awaiting_human' deliberately
// reuses 'user-plus', the same icon NODE_TYPE_ICONS gives the 'human_input'
// node type, so a paused run reads as "waiting on a person" the same way
// everywhere it appears.
export const NODE_RUN_STATUS_ICONS: Record<NodeRunStatus, string> = {
	pending: 'clock',
	running: 'sync',
	succeeded: 'check-circle',
	failed: 'x-circle',
	skipped: 'minus-circle',
	awaiting_human: 'user-plus'
};

// Mirrors the existing status->color convention from the run-history panel
// (statusClass in +page.svelte): agent-magenta for failure, amber for
// awaiting_human. 'succeeded' and 'skipped' have no equivalent there since
// that panel never needed per-node-type nuance beyond "failed" -- green and
// neutral are the natural reads for those two.
export const NODE_RUN_STATUS_COLOR_CLASS: Record<NodeRunStatus, string> = {
	pending: 'text-neutral-500 dark:text-neutral-400',
	running: 'text-agent-cyan-600 dark:text-agent-cyan-400',
	succeeded: 'text-green-600 dark:text-green-400',
	failed: 'text-agent-magenta-700 dark:text-agent-magenta-400',
	skipped: 'text-neutral-400 dark:text-neutral-500',
	awaiting_human: 'text-amber-600 dark:text-amber-400'
};
