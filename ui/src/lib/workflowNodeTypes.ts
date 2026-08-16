import type { WorkflowNodeType } from './api/workflows';

// Plain-language step names (09-copy-deck.md): "If", never "Conditional";
// "Wait for a person", never "Human input".
export const NODE_TYPE_LABELS: Record<WorkflowNodeType, string> = {
	agent: 'Agent',
	transform: 'Transform',
	summarize: 'Summarize',
	conditional: 'If',
	merge: 'Merge',
	human_input: 'Wait for a person',
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
