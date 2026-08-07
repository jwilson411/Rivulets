// Workspace-level usage/cost dashboard client (#28, api/usage.py). Read-only
// aggregation across every agent's runs, over a selectable time window.

import { api } from './client';
import { auth } from './auth.svelte';

export type UsageRange = 'day' | 'week' | 'month';

export interface UsageByAgent {
	agent_id: string;
	agent_name: string;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	cost_usd: number | null;
	run_count: number;
}

export interface UsageByModel {
	model: string;
	tier: string | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	cost_usd: number | null;
	run_count: number;
}

export interface Usage {
	range: UsageRange;
	since: string;
	total_input_tokens: number;
	total_output_tokens: number;
	total_tokens: number;
	total_cost_usd: number;
	cost_incomplete: boolean;
	run_count: number;
	by_agent: UsageByAgent[];
	by_model: UsageByModel[];
}

export const usage = {
	get: (range: UsageRange = 'week') =>
		api.get<Usage>(`/usage?range=${range}`, auth.token ?? undefined)
};
