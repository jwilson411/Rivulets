// Browser-mode component test for Runs (06-screens.md → Runs, mockup 2g):
// one timeline per human message / slash command / schedule fire, expanding
// into steps — the word "span" never appears in the UI (09-copy-deck.md).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RunsPage from './+page.svelte';
import { runs, type RunTrace, type RunSpan } from '$lib/api/runs';

vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		let out = path;
		if (params) {
			for (const [key, value] of Object.entries(params)) out = out.replace(`[${key}]`, value);
		}
		return out;
	}
}));

vi.mock('$lib/api/runs', () => ({
	runs: { list: vi.fn(), get: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

const completedTrace: RunTrace = {
	id: 'trace-1',
	trigger_type: 'message',
	label: 'Riley posted in #launch-readiness',
	rivulet_id: 'riv-1',
	channel_id: 'chan-1',
	status: 'completed',
	span_count: 4,
	total_cost_usd: 0.021,
	total_tokens: 4065,
	started_at: new Date().toISOString(),
	completed_at: new Date().toISOString()
};

const dispatchSpan: RunSpan = {
	id: 'span-1',
	parent_span_id: null,
	span_type: 'dispatch_decision',
	entity_id: null,
	name: 'dispatch',
	status: 'completed',
	model: null,
	cost_usd: null,
	total_tokens: null,
	started_at: new Date().toISOString(),
	completed_at: new Date().toISOString(),
	duration_ms: 40,
	tool_calls: []
};

const agentSpan: RunSpan = {
	id: 'span-2',
	parent_span_id: null,
	span_type: 'agent_run',
	entity_id: 'agent-1',
	name: 'Coder',
	status: 'completed',
	model: null,
	cost_usd: 0.02,
	total_tokens: 1204,
	started_at: new Date().toISOString(),
	completed_at: new Date().toISOString(),
	duration_ms: 2100,
	tool_calls: [
		{
			id: 'call-1',
			tool_name: 'web_search',
			sensitive: false,
			status: 'success',
			arguments_json: null,
			result_summary: null,
			duration_ms: 300,
			created_at: new Date().toISOString()
		}
	]
};

describe('runs/+page.svelte', () => {
	it('lists run timelines with a status pill, step count, and cost — never "spans"', async () => {
		vi.mocked(runs.list).mockResolvedValue([completedTrace]);

		render(RunsPage);

		await expect.element(page.getByText('Riley posted in #launch-readiness')).toBeInTheDocument();
		await expect.element(page.getByText('Completed')).toBeInTheDocument();
		await expect.element(page.getByText(/4 steps/)).toBeInTheDocument();
		await expect.element(page.getByText(/span/i, { exact: false }).first()).not.toBeInTheDocument();
	});

	it('expands a run into named steps with an Open conversation link', async () => {
		vi.mocked(runs.list).mockResolvedValue([completedTrace]);
		vi.mocked(runs.get).mockResolvedValue({
			...completedTrace,
			spans: [dispatchSpan, agentSpan]
		});

		render(RunsPage);
		await page.getByText('Riley posted in #launch-readiness').click();

		expect(runs.get).toHaveBeenCalledWith('trace-1');
		await expect.element(page.getByText('Dispatch')).toBeInTheDocument();
		await expect.element(page.getByText('Coder')).toBeInTheDocument();
		await expect.element(page.getByText('web_search')).toBeInTheDocument();
		const link = page.getByRole('link', { name: 'Open conversation' });
		await expect.element(link).toHaveAttribute('href', '/channels/chan-1/rivulets/riv-1');
	});

	it('shows the empty state when nothing has run', async () => {
		vi.mocked(runs.list).mockResolvedValue([]);

		render(RunsPage);

		await expect
			.element(page.getByText('Nothing has run yet. Send a message or fire a workflow.'))
			.toBeInTheDocument();
	});

	it('shows a quiet error with retry when runs fail to load', async () => {
		vi.mocked(runs.list).mockRejectedValue(new Error('boom'));

		render(RunsPage);

		await expect.element(page.getByText("Couldn't load runs.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
