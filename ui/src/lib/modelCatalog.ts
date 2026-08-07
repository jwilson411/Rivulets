// Curated model picker options per provider kind (FR-1.4's minimum set).
// IDs here are ones already exercised elsewhere in the codebase (server
// tests, dispatch/rule_generation.py's per-provider defaults) rather than
// an exhaustive/self-maintained catalog — each group also gets a "Custom"
// slot so a model that isn't listed (or a brand new release) is never
// unreachable, and `openai_compatible` is custom-only since its model
// names are whatever the self-hosted endpoint calls them.

import type { ProviderKind } from './api/providers';

export interface ModelOption {
	id: string;
	label: string;
}

export const MODEL_CATALOG: Record<ProviderKind, ModelOption[]> = {
	anthropic: [
		{ id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 — fast, cheap' },
		{ id: 'claude-3-5-haiku-latest', label: 'Claude 3.5 Haiku — fast, cheap' },
		{ id: 'claude-opus-4-1', label: 'Claude Opus 4.1 — most capable' }
	],
	openai: [
		{ id: 'gpt-4o-mini', label: 'GPT-4o mini — fast, cheap' },
		{ id: 'gpt-4o', label: 'GPT-4o — capable' },
		{ id: 'o3-mini', label: 'o3-mini — reasoning' }
	],
	deepseek: [
		{ id: 'deepseek-chat', label: 'DeepSeek Chat — fast, cheap' },
		{ id: 'deepseek-reasoner', label: 'DeepSeek Reasoner — reasoning' }
	],
	google: [
		{ id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash — fast, cheap' },
		{ id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash — fast, cheap' }
	],
	mistral: [
		{ id: 'mistral-large-latest', label: 'Mistral Large — most capable' },
		{ id: 'mistral-small-latest', label: 'Mistral Small — fast, cheap' }
	],
	groq: [
		{ id: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B — capable' },
		{ id: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B — fast, cheap' }
	],
	xai: [{ id: 'grok-4-1-fast-non-reasoning-latest', label: 'Grok 4.1 Fast — fast, cheap' }],
	qwen: [{ id: 'qwen-plus', label: 'Qwen Plus — capable' }],
	cohere: [{ id: 'command-a-03-2025', label: 'Command A — most capable' }],
	// Local models pulled by the user's own Ollama install -- names are
	// whatever they've pulled, same reasoning as openai_compatible below.
	ollama: [],
	openai_compatible: []
};

export const CUSTOM_MODEL_VALUE = '__custom__';

// The stored `agent.model` sentinel for Auto mode (#23, server's
// agentos/models.py AUTO_MODEL) -- the model is picked fresh per message
// instead of being fixed at agent creation. AUTO_MODEL_VALUE is a
// distinct <option> value only so it never collides with a real
// "provider:model" string in the same <select>.
export const AUTO_MODEL = 'auto';
export const AUTO_MODEL_VALUE = '__auto__';
