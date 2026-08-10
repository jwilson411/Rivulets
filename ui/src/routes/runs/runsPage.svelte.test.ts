// Browser-mode component test (see workflows/workflowsPage.svelte.test.ts,
// the closest precedent for an expandable-list + $app/paths resolve() page).
// This route depends only on $lib/api/runs.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import RunsPage from './+page.svelte';
import { runs, type RunTrace, type RunTraceDetail, type ToolCall } from '$lib/api/runs';

vi.mock('$lib/api/runs', () => ({
	runs: {
		list: vi.fn(),
		get: vi.fn()
	}
}));

vi.mock('$app/paths', () => ({
	resolve: (path: string, params?: Record<string, string>) => {
		if (!params) return path;
		let resolved = path;
		for (const [key, value] of Object.entries(params)) {
			resolved = resolved.replace(`[${key}]`, value);
		}
		return resolved;
	}
}));

const messageTrace: RunTrace = {
	id: 'trace-1',
	trigger_type: 'message',
	label: 'hello there',
	rivulet_id: 'riv-1',
	channel_id: 'chan-1',
	status: 'completed',
	span_count: 2,
	total_cost_usd: 0.0032,
	total_tokens: 120,
	started_at: '2026-08-09T06:00:00Z',
	completed_at: '2026-08-09T06:00:02Z'
};

const toolCall: ToolCall = {
	id: 'call-1',
	tool_name: 'execute_python',
	sensitive: true,
	status: 'success',
	arguments_json: '{"code": "print(1)"}',
	result_summary: '1',
	duration_ms: 250,
	created_at: '2026-08-09T06:00:01Z'
};

const traceDetail: RunTraceDetail = {
	...messageTrace,
	spans: [
		{
			id: 'span-1',
			parent_span_id: null,
			span_type: 'dispatch_decision',
			entity_id: 'dd-1',
			name: 'dispatch (deterministic)',
			status: 'completed',
			model: null,
			cost_usd: null,
			total_tokens: null,
			started_at: '2026-08-09T06:00:00Z',
			completed_at: '2026-08-09T06:00:02Z',
			duration_ms: 2000,
			tool_calls: []
		},
		{
			id: 'span-2',
			parent_span_id: 'span-1',
			span_type: 'agent_run',
			entity_id: 'ar-1',
			name: 'Researcher',
			status: 'completed',
			model: 'anthropic:claude-haiku-4-5-20251001',
			cost_usd: 0.0032,
			total_tokens: 120,
			tool_calls: [toolCall],
			started_at: '2026-08-09T06:00:00Z',
			completed_at: '2026-08-09T06:00:02Z',
			duration_ms: 1800
		}
	]
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('runs/+page.svelte', () => {
	it('lists recent traces', async () => {
		vi.mocked(runs.list).mockResolvedValue([messageTrace]);

		render(RunsPage);

		await expect.element(page.getByText('hello there')).toBeInTheDocument();
		await expect.element(page.getByText('2 spans')).toBeInTheDocument();
		await expect.element(page.getByText('completed')).toBeInTheDocument();
	});

	it('shows an error when the list fails to load', async () => {
		vi.mocked(runs.list).mockRejectedValueOnce(new Error('Failed to load runs'));

		render(RunsPage);

		await expect.element(page.getByText('Failed to load runs')).toBeInTheDocument();
	});

	it('shows an empty state when there are no runs yet', async () => {
		vi.mocked(runs.list).mockResolvedValue([]);

		render(RunsPage);

		await expect
			.element(
				page.getByText(
					'No runs recorded yet — this fills in as messages are dispatched or workflows fire.'
				)
			)
			.toBeInTheDocument();
	});

	it('expands a trace to show its span tree, nested under its parent', async () => {
		vi.mocked(runs.list).mockResolvedValue([messageTrace]);
		vi.mocked(runs.get).mockResolvedValue(traceDetail);

		render(RunsPage);

		await expect.element(page.getByText('hello there')).toBeInTheDocument();
		await page.getByText('hello there').click();

		await expect.element(page.getByText('dispatch (deterministic)')).toBeInTheDocument();
		await expect.element(page.getByText('Researcher')).toBeInTheDocument();
		await expect.element(page.getByText('1.8s', { exact: false })).toBeInTheDocument();
		expect(runs.get).toHaveBeenCalledWith('trace-1');
	});

	it('shows a tool call nested under its agent_run span, flagged sensitive', async () => {
		vi.mocked(runs.list).mockResolvedValue([messageTrace]);
		vi.mocked(runs.get).mockResolvedValue(traceDetail);

		render(RunsPage);
		await page.getByText('hello there').click();

		await expect.element(page.getByText('execute_python')).toBeInTheDocument();
		await expect.element(page.getByText('sensitive')).toBeInTheDocument();
	});

	it('links an expanded trace back to its rivulet', async () => {
		vi.mocked(runs.list).mockResolvedValue([messageTrace]);
		vi.mocked(runs.get).mockResolvedValue(traceDetail);

		render(RunsPage);
		await page.getByText('hello there').click();

		const link = page.getByText('View rivulet');
		await expect.element(link).toBeInTheDocument();
		await expect.element(link).toHaveAttribute('href', '/channels/chan-1/rivulets/riv-1');
	});
});
