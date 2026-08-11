import type { WorkflowNodeType } from './api/workflows';

export const NODE_TYPE_LABELS: Record<WorkflowNodeType, string> = {
	agent: 'Agent',
	transform: 'Transform',
	summarize: 'Summarize',
	conditional: 'Conditional',
	merge: 'Merge',
	human_input: 'Human input',
	workflow: 'Workflow'
};

// Names from Icon.svelte's fixed set -- all already exist, no new icons needed.
export const NODE_TYPE_ICONS: Record<WorkflowNodeType, string> = {
	agent: 'robot',
	transform: 'wrench',
	summarize: 'book',
	conditional: 'route',
	merge: 'inbox-check',
	human_input: 'user-plus',
	workflow: 'workflow'
};
